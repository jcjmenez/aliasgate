"""The `aliasgate` command: `serve`, `check`, `demo` and `version`.

Exit codes: 0 success, 2 configuration error (or invalid arguments), 1 anything else. Errors
name files, fields and entry positions, never configuration or dictionary values. `demo` is the
only code path that prints detected values, and only with `--show-mapping`.
"""

import argparse
import ipaddress
import json
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

import uvicorn
import yaml
from pydantic import ValidationError

from aliasgate import __version__
from aliasgate.config import ListenSettings, Settings, load_settings, load_terms
from aliasgate.detectors.dictionary import Term
from aliasgate.engine import DetectionError, Pseudonymizer
from aliasgate.factory import build_pseudonymizer
from aliasgate.proxy.app import configure_logging, create_app

EXIT_OK: Final = 0
EXIT_FAILURE: Final = 1
EXIT_CONFIGURATION: Final = 2

_NOT_LOOPBACK: Final = (
    "warning: listening on {host}, which is not a loopback address. The gateway has no client"
    " authentication and must sit behind a trusted network boundary (docs/deployment.md)."
)


class _ConfigurationError(Exception):
    """Invalid configuration or dictionaries; each problem names a field, file or position."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__(f"{len(problems)} configuration problem(s)")
        self.problems = tuple(problems)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command and return its exit code: 0 success, 2 configuration error, 1 otherwise."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # --help (0) or invalid arguments (2); argparse printed why
        return exc.code if isinstance(exc.code, int) else EXIT_CONFIGURATION
    command: Callable[[argparse.Namespace], int] = args.run
    try:
        return command(args)
    except _ConfigurationError as exc:
        for problem in exc.problems:
            _error(problem)
        return EXIT_CONFIGURATION
    except DetectionError as exc:  # names the detector, never text
        _error(str(exc))
        return EXIT_FAILURE
    except Exception as exc:  # any other message may hold text
        _error(f"unexpected failure ({type(exc).__name__})")
        return EXIT_FAILURE


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aliasgate", description="Reversible pseudonymisation gateway for LLM APIs."
    )
    commands = parser.add_subparsers(title="commands", metavar="COMMAND", required=True)

    serve = commands.add_parser(
        "serve", help="serve the gateway", description="Serve the gateway until interrupted."
    )
    _config_argument(serve)
    serve.add_argument("--host", help="address to listen on; overrides listen.host")
    serve.add_argument("--port", type=int, help="port to listen on; overrides listen.port")
    serve.set_defaults(run=_serve)

    check = commands.add_parser(
        "check",
        help="validate the configuration and dictionaries",
        description="Validate the configuration and dictionaries, build the detectors (loading "
        "the model if enabled) and print a summary.",
    )
    _config_argument(check)
    check.set_defaults(run=_check)

    demo = commands.add_parser(
        "demo",
        help="pseudonymise standard input, for tuning dictionaries",
        description="Pseudonymise standard input and print the result. Entity counts go to "
        "standard error. A local debugging aid: never run it on shared terminals or in CI logs "
        "with --show-mapping.",
    )
    _config_argument(demo)
    demo.add_argument(
        "--show-mapping",
        action="store_true",
        help="also print each placeholder and the value it replaced to standard error",
    )
    demo.set_defaults(run=_demo)

    version = commands.add_parser("version", help="print the version")
    version.set_defaults(run=_version)
    return parser


def _config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        metavar="PATH",
        help="YAML configuration file; ALIASGATE_* environment variables override it",
    )


def _serve(args: argparse.Namespace) -> int:
    settings = _with_listen(_load_settings(args.config), args.host, args.port)
    _load_dictionaries(settings)
    configure_logging(settings.logging.level)
    if not _is_loopback(settings.listen.host):
        print(_NOT_LOOPBACK.format(host=settings.listen.host), file=sys.stderr)
    app = create_app(settings, pseudonymizer=_build(settings))
    # The gateway logs one line per request itself; uvicorn's access log would add the path
    # and query string of every request.
    uvicorn.run(
        app,
        host=settings.listen.host,
        port=settings.listen.port,
        log_config=None,
        access_log=False,
    )
    return EXIT_OK


def _check(args: argparse.Namespace) -> int:
    settings = _load_settings(args.config)
    dictionaries = _load_dictionaries(settings)
    pseudonymizer = _build(settings)
    print(_summary(args.config, settings, dictionaries, pseudonymizer))
    return EXIT_OK


def _demo(args: argparse.Namespace) -> int:
    settings = _load_settings(args.config)
    _load_dictionaries(settings)
    pseudonymizer = _build(settings)
    batch = pseudonymizer.pseudonymize([sys.stdin.read()])
    sys.stdout.write(batch.texts[0])
    sys.stdout.flush()
    entities = batch.mapping.entities()
    if not entities:
        print("no entities detected", file=sys.stderr)
        return EXIT_OK
    found = Counter(entity.entity_type for entity in entities)
    replaced: Counter[str] = Counter()
    for (entity_type, _source), count in batch.stats.entities_by_type_and_source.items():
        replaced[entity_type] += count
    width = max(len("entity type"), *(len(entity_type) for entity_type in found))
    print(f"{'entity type':<{width}}  entities  occurrences", file=sys.stderr)
    for entity_type in sorted(found):
        print(
            f"{entity_type:<{width}}  {found[entity_type]:>8}  {replaced[entity_type]:>11}",
            file=sys.stderr,
        )
    if args.show_mapping:
        print("\nplaceholder  value", file=sys.stderr)
        for entity in entities:
            surface = json.dumps(entity.surface, ensure_ascii=False)
            print(f"{entity.placeholder}  {surface}", file=sys.stderr)
    return EXIT_OK


def _version(args: argparse.Namespace) -> int:
    print(f"aliasgate {__version__}")
    return EXIT_OK


def _load_settings(path: Path) -> Settings:
    try:
        return load_settings(path)
    except yaml.YAMLError as exc:
        # The parser's message quotes the offending line.
        raise _ConfigurationError(
            [f"the configuration file {path} is not valid YAML{_yaml_position(exc)}"]
        ) from None
    except ValidationError as exc:
        raise _ConfigurationError(_field_problems(exc)) from None
    except OSError as exc:
        raise _ConfigurationError([_unreadable(path, exc)]) from None
    except ValueError as exc:  # an ALIASGATE_* variable that does not parse; names the field
        raise _ConfigurationError([str(exc)]) from None


def _with_listen(settings: Settings, host: str | None, port: int | None) -> Settings:
    if host is None and port is None:
        return settings
    listen = settings.listen.model_dump()
    if host is not None:
        listen["host"] = host
    if port is not None:
        listen["port"] = port
    try:
        validated = ListenSettings.model_validate(listen)
    except ValidationError as exc:
        raise _ConfigurationError(_field_problems(exc, prefix=("listen",))) from None
    return settings.model_copy(update={"listen": validated})


def _load_dictionaries(settings: Settings) -> list[list[Term]]:
    """Every dictionary file, parsed; the problems of all files are reported together."""
    loaded: list[list[Term]] = []
    problems: list[str] = []
    for path in settings.detection.dictionary.files:
        try:
            loaded.append(load_terms(path))
        except OSError as exc:
            problems.append(_unreadable(path, exc))
        except ValueError as exc:  # names the file and the entry, never a term
            problems.append(str(exc))
    if problems:
        raise _ConfigurationError(problems)
    return loaded


def _build(settings: Settings) -> Pseudonymizer:
    try:
        return build_pseudonymizer(settings)
    except (ValueError, RuntimeError) as exc:
        # Conflicting dictionary terms (named by position), a missing ML extra or a window the
        # model cannot take. No request text exists yet, and terms are never in these messages.
        raise _ConfigurationError([str(exc)]) from None


def _summary(
    path: Path,
    settings: Settings,
    dictionaries: Sequence[Sequence[Term]],
    pseudonymizer: Pseudonymizer,
) -> str:
    detection = settings.detection
    ml = detection.ml
    terms = [term for terms in dictionaries for term in terms]
    types = {*detection.rules.enabled_types, *(term.entity_type for term in terms)}
    if ml.enabled:
        types.update(ml.labels.values())
        revision = "a local directory" if ml.revision is None else f"revision {ml.revision}"
        ml_line = f"ml: {ml.model} at {revision}, threshold {ml.threshold}"
    else:
        ml_line = "ml: off"
    dictionary = (
        f"{_plural(len(terms), 'term')} in {_plural(len(dictionaries), 'file')}"
        if dictionaries
        else "no terms"
    )
    upstreams = settings.upstreams
    policy = settings.policy
    return "\n".join(
        [
            f"configuration: {path} is valid",
            "detectors: " + ", ".join(detector.name for detector in pseudonymizer.detectors),
            f"dictionary: {dictionary}",
            "entity types: " + (", ".join(sorted(types)) or "none"),
            ml_line,
            "upstreams:",
            f"  openai: {upstreams.openai.base_url}",
            f"  anthropic: {upstreams.anthropic.base_url}",
            "passthrough: " + (", ".join(upstreams.passthrough_paths) or "none"),
            f"listen: {settings.listen.host} port {settings.listen.port}",
            "policy: "
            f"unsupported_content={policy.unsupported_content}, "
            f"strip_user_identifiers={_on(policy.strip_user_identifiers)}, "
            f"tool_definitions={_on(policy.tool_definitions)}, "
            f"leak_audit={_on(policy.leak_audit)}",
            f"propagation: {_on(detection.propagate)}",
            f"person tokens: {_on(detection.person_tokens)}",
            f"metrics: {_on(settings.metrics.enabled)}",
        ]
    )


def _field_problems(exc: ValidationError, prefix: tuple[str, ...] = ()) -> list[str]:
    """One line per error: the field path and pydantic's reason, which never holds the input
    (every settings model sets `hide_input_in_errors`)."""
    problems = []
    for error in exc.errors(include_url=False, include_input=False, include_context=False):
        location = ".".join(str(part) for part in (*prefix, *error["loc"])) or "(top level)"
        problems.append(f"{location}: {error['msg']}")
    return problems


def _yaml_position(exc: yaml.YAMLError) -> str:
    mark = getattr(exc, "problem_mark", None)
    return f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""


def _unreadable(path: Path, exc: OSError) -> str:
    return f"cannot read {path}: {exc.strerror or type(exc).__name__}"


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _on(flag: bool) -> str:
    return "on" if flag else "off"


def _error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)

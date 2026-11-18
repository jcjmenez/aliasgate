"""Gateway configuration (architecture §9) and dictionary files.

`load_settings` reads one YAML file, overlays `ALIASGATE_*` environment variables and returns
an immutable, validated `Settings`. Every section forbids unknown keys, and validation errors
never repeat input values. `Settings` holds no secrets: the gateway forwards client
credentials and stores none.
"""

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Final, Literal
from urllib.parse import unquote, urlsplit

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    create_model,
    field_validator,
)
from pydantic_settings import BaseSettings, EnvSettingsSource
from pydantic_settings.sources.utils import parse_env_vars

from aliasgate.detectors.dictionary import Term
from aliasgate.mapping import normalize_key
from aliasgate.types import validate_entity_type

_ENV_PREFIX: Final = "ALIASGATE_"
_ENV_NESTED_DELIMITER: Final = "__"
_BASE_DIR: Final = "base_dir"
# One path segment as RFC 3986 §3.3 writes it: unreserved characters, sub-delimiters, ":" and
# "@", and percent-encoded octets. httpx sends such a segment exactly as written.
_PATH_SEGMENT: Final = re.compile(r"(?:[A-Za-z0-9\-._~!$&'()*+,;=:@]|%[0-9A-Fa-f]{2})+")
_DOT_SEGMENTS: Final = frozenset({".", ".."})


def _base_url(value: str) -> str:
    """An http(s) URL with an optional base path, returned without trailing slashes, such that
    appending a request path to it gives exactly the URL that is sent (architecture §7)."""
    # urlsplit drops tabs and newlines, and an empty "?" or "#" leaves no query or fragment, so
    # these are checked on the raw value: after concatenation they would move the request path.
    if any(char in "?#" or char.isspace() or not char.isprintable() for char in value):
        raise ValueError("must not contain '?', '#', whitespace or control characters")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("must be an http or https URL with a host")
    if parts.username is not None or parts.password is not None:
        raise ValueError("must not contain credentials")
    _ = parts.port  # raises ValueError for a malformed port
    for segment in parts.path.rstrip("/").split("/")[1:]:
        if not segment:
            raise ValueError("the path must not have empty segments")
        if not _PATH_SEGMENT.fullmatch(segment):
            raise ValueError(
                "the path may only contain letters, digits, -._~!$&'()*+,;=:@ and %XX escapes"
            )
        if unquote(segment) in _DOT_SEGMENTS:
            raise ValueError("the path must not have '.' or '..' segments")
    return value.rstrip("/")


BaseUrl = Annotated[str, AfterValidator(_base_url)]
EntityType = Annotated[str, AfterValidator(validate_entity_type)]
PhoneRegion = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
# "<upstream> <METHOD> <path>": the upstream is a field name of UpstreamsSettings.
PassthroughPath = Annotated[
    str, StringConstraints(pattern=r"^(openai|anthropic) (GET|POST|DELETE) /\S*$")
]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class ListenSettings(_Section):
    """Address the gateway serves on; loopback unless chosen otherwise."""

    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    max_request_bytes: int = Field(default=10_485_760, ge=1)


class UpstreamSettings(_Section):
    """One provider; `base_url` may end in a base path and has no trailing slash, so
    `base_url + request path` is the upstream URL."""

    base_url: BaseUrl


class UpstreamsSettings(_Section):
    """Providers the gateway forwards to, and the extra paths it may forward unchanged.

    A passthrough entry is `"<upstream> <METHOD> <path>"`, for example
    `"anthropic GET /v1/models"`; each method and path appears in at most one entry, so a
    request names exactly one upstream.
    """

    openai: UpstreamSettings = UpstreamSettings(base_url="https://api.openai.com")
    anthropic: UpstreamSettings = UpstreamSettings(base_url="https://api.anthropic.com")
    timeout_seconds: float = Field(default=600, gt=0)
    passthrough_paths: tuple[PassthroughPath, ...] = ()

    @field_validator("passthrough_paths")
    @classmethod
    def _one_upstream_per_path(cls, entries: tuple[str, ...]) -> tuple[str, ...]:
        routes = [entry.split(" ", 1)[1] for entry in entries]
        if len(set(routes)) != len(routes):
            raise ValueError("each method and path may appear in only one entry")
        return entries


class RulesSettings(_Section):
    """Rule detector: which rule types run, and the regions for national phone formats."""

    enabled_types: tuple[EntityType, ...] = (
        "EMAIL",
        "PHONE",
        "IBAN",
        "CREDIT_CARD",
        "NATIONAL_ID",
        "TAX_ID",
        "IP_ADDRESS",
        "SECRET",
    )
    phone_regions: tuple[PhoneRegion, ...] = ("ES", "US")


class DictionarySettings(_Section):
    """Dictionary files; relative paths are resolved against the configuration file."""

    files: tuple[Path, ...] = ()

    @field_validator("files")
    @classmethod
    def _resolve_relative_paths(
        cls, files: tuple[Path, ...], info: ValidationInfo
    ) -> tuple[Path, ...]:
        context = info.context if isinstance(info.context, dict) else {}
        base = context.get(_BASE_DIR, Path.cwd())
        return tuple(file if file.is_absolute() else base / file for file in files)


class MLSettings(_Section):
    """ML detector settings. Accepted and validated now; used from slice 09."""

    enabled: bool = False
    model: str | None = None
    threshold: float = Field(default=0.4, ge=0.0, le=1.0)
    labels: dict[str, EntityType] = Field(default_factory=dict)


class DetectionSettings(_Section):
    """Detection layers, allow list and propagation."""

    rules: RulesSettings = RulesSettings()
    dictionary: DictionarySettings = DictionarySettings()
    allow_list: tuple[str, ...] = ()
    ml: MLSettings = MLSettings()
    propagate: bool = True
    min_propagation_length: int = Field(default=3, ge=1)


class PolicySettings(_Section):
    """What happens to content the gateway cannot pseudonymise or should not forward."""

    unsupported_content: Literal["block", "pass"] = "block"
    strip_user_identifiers: bool = True
    tool_definitions: bool = True


class LoggingSettings(_Section):
    """Log level. Logs never contain text (architecture §7.1)."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


class Settings(_Section):
    """Complete gateway configuration: immutable, validated, free of secrets."""

    listen: ListenSettings = ListenSettings()
    upstreams: UpstreamsSettings = UpstreamsSettings()
    detection: DetectionSettings = DetectionSettings()
    policy: PolicySettings = PolicySettings()
    logging: LoggingSettings = LoggingSettings()


# The fields of Settings on a BaseSettings class, which is what pydantic-settings needs to parse
# environment variables. Settings itself stays a plain model that never reads the environment.
_SETTINGS_FIELDS: Final[dict[str, Any]] = {
    name: (field.annotation, field) for name, field in Settings.model_fields.items()
}
_EnvironmentShape = create_model("_EnvironmentShape", __base__=BaseSettings, **_SETTINGS_FIELDS)


class _EnvironmentSource(EnvSettingsSource):
    """Parses `ALIASGATE_*` variables from a given mapping instead of `os.environ`."""

    def __init__(self, env: Mapping[str, str]) -> None:
        self._given_env = env
        super().__init__(
            _EnvironmentShape,
            case_sensitive=False,
            env_prefix=_ENV_PREFIX,
            env_nested_delimiter=_ENV_NESTED_DELIMITER,
        )

    def _load_env_vars(self) -> Mapping[str, str | None]:
        return parse_env_vars(
            self._given_env, self.case_sensitive, self.env_ignore_empty, self.env_parse_none_str
        )


def _environment_data(env: Mapping[str, str]) -> dict[str, Any]:
    data = dict(_EnvironmentSource(env)())
    for name, value in env.items():
        if not name.upper().startswith(_ENV_PREFIX):
            continue
        section = name[len(_ENV_PREFIX) :].split(_ENV_NESTED_DELIMITER, 1)[0].lower()
        if section not in Settings.model_fields:
            # pydantic-settings skips variables that match no field; keep them so that
            # extra="forbid" turns a misspelled variable into a start-up error.
            data[name] = value
    return data


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(value, Mapping) and isinstance(current, Mapping):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def load_settings(path: Path | None, env: Mapping[str, str] = os.environ) -> Settings:
    """Load YAML (if a path is given), overlay ALIASGATE_* environment variables, validate."""
    file_data: Any = {}
    if path is not None:
        with path.open(encoding="utf-8") as stream:
            file_data = yaml.safe_load(stream)
        if file_data is None:
            file_data = {}
    env_data = _environment_data(env)
    data = _deep_merge(file_data, env_data) if isinstance(file_data, Mapping) else file_data
    base_dir = path.absolute().parent if path is not None else Path.cwd()
    return Settings.model_validate(data, context={_BASE_DIR: base_dir})


def load_terms(path: Path) -> list[Term]:
    """Parse and validate a dictionary file; errors name entry positions, never term content."""
    with path.open(encoding="utf-8") as stream:
        try:
            data = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            # The exception text quotes the offending line, which may contain a term.
            mark = getattr(exc, "problem_mark", None)
            where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
            raise ValueError(f"dictionary file {path} is not valid YAML{where}") from None
    if not isinstance(data, dict) or set(data) != {"terms"}:
        raise ValueError(f"dictionary file {path} must be a mapping with a single 'terms' key")
    entries = data["terms"] if data["terms"] is not None else []
    if not isinstance(entries, list):
        raise ValueError(f"dictionary file {path}: 'terms' must be a list")
    return [_term(path, position, entry) for position, entry in enumerate(entries, start=1)]


def _term(path: Path, position: int, entry: object) -> Term:
    where = f"dictionary file {path}, entry {position}"
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: must be a mapping with 'term' and 'type'")
    if set(entry) - {"term", "type"}:
        raise ValueError(f"{where}: only 'term' and 'type' are allowed")
    for key in ("term", "type"):
        if key not in entry:
            raise ValueError(f"{where}: missing '{key}'")
        if not isinstance(entry[key], str):
            raise ValueError(f"{where}: '{key}' must be a string")
    term, entity_type = entry["term"], entry["type"]
    try:
        validate_entity_type(entity_type)
    except ValueError as exc:
        raise ValueError(f"{where}: {exc}") from None
    if not normalize_key(term):
        raise ValueError(f"{where}: 'term' is empty")
    return Term(term, entity_type)

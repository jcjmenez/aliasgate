import importlib
import importlib.metadata
import io
import os
import re
import runpy
import sys
import warnings
from pathlib import Path
from typing import Any

import pytest
import uvicorn
import yaml
from starlette.applications import Starlette

import aliasgate
from aliasgate import cli, factory
from aliasgate.detectors.rules import RuleDetector
from aliasgate.engine import Pseudonymizer

EXAMPLE_CONFIG = Path(__file__).parents[1] / "examples" / "aliasgate.example.yaml"
# Synthetic secrets of the broken dictionary fixture: they must never reach the output.
BROKEN_TERMS = ("Project Nightingale", "Globex Iberia S.L.")
DEMO_TEXT = "Contact Juan Pérez at juan.perez@example.com"
WARNING = "not a loopback address"


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """ALIASGATE_* variables of the developer's shell must not change the results."""
    for name in list(os.environ):
        if name.upper().startswith("ALIASGATE_"):
            monkeypatch.delenv(name)


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Record uvicorn.run and configure_logging instead of serving or touching global logging."""
    recorded: dict[str, Any] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        recorded["app"] = app
        recorded["run"] = kwargs

    def fake_configure_logging(level: str) -> None:
        recorded["level"] = level

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(cli, "configure_logging", fake_configure_logging)
    return recorded


def _write(path: Path, data: object) -> Path:
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _config(tmp_path: Path, **sections: object) -> Path:
    return _write(tmp_path / "aliasgate.yaml", sections)


def _dictionary_config(tmp_path: Path, *terms: tuple[str, str]) -> Path:
    _write(tmp_path / "dictionary.yaml", {"terms": [{"term": t, "type": k} for t, k in terms]})
    return _config(tmp_path, detection={"dictionary": {"files": ["dictionary.yaml"]}})


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def _demo(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    stdin: str,
    *argv: str,
) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    return _run(capsys, "demo", *argv)


# version


def test_version_prints_the_package_version_and_exits_0(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, err = _run(capsys, "version")
    assert code == 0
    assert out == f"aliasgate {aliasgate.__version__}\n"
    assert err == ""


def test_package_version_is_0_1_0_and_matches_the_installed_distribution() -> None:
    assert aliasgate.__version__ == "0.1.0"
    assert importlib.metadata.version("aliasgate") == aliasgate.__version__


def test_the_aliasgate_console_script_runs_cli_main() -> None:
    (script,) = importlib.metadata.entry_points(group="console_scripts", name="aliasgate")
    assert script.value == "aliasgate.cli:main"


def test_importing_the_main_module_runs_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    module = importlib.import_module("aliasgate.__main__")
    assert module.main is cli.main
    assert capsys.readouterr() == ("", "")


def test_running_the_package_as_a_module_runs_the_cli(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["aliasgate", "version"])
    with warnings.catch_warnings():
        # runpy warns that aliasgate.__main__ is already imported; that is expected here.
        warnings.simplefilter("ignore", RuntimeWarning)
        with pytest.raises(SystemExit) as exit_info:
            runpy.run_module("aliasgate", run_name="__main__")
    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"aliasgate {aliasgate.__version__}\n"


# argument errors


def test_no_command_is_a_usage_error_with_exit_code_2(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = _run(capsys)
    assert code == 2
    assert "usage: aliasgate" in err


@pytest.mark.parametrize("command", ["serve", "check", "demo"])
def test_commands_that_need_a_configuration_require_config(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = _run(capsys, command)
    assert code == 2
    assert "--config" in err


def test_help_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(capsys, "--help")
    assert code == 0
    for command in ("serve", "check", "demo", "version"):
        assert command in out


# check


def test_check_with_the_example_configuration_prints_a_summary_and_exits_0(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, err = _run(capsys, "check", "--config", str(EXAMPLE_CONFIG))
    assert code == 0
    assert err == ""
    assert "detectors: literal, dictionary, rules" in out
    assert "dictionary: 3 terms in 1 file" in out
    assert re.search(r"entity types: .*\bEMAIL\b.*\bORG\b.*\bPROJECT\b", out)
    assert "openai: https://api.openai.com" in out
    assert "anthropic: https://api.anthropic.com" in out
    # The summary counts terms; it never lists them.
    for term in ("ACME Corporation", "Globex S.L.", "Project Falcon"):
        assert term not in out


def test_check_summary_lists_passthrough_entries_and_the_ml_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(factory, "load_gliner", lambda model_id, revision: _NoEntities())
    config = _config(
        tmp_path,
        upstreams={"passthrough_paths": ["anthropic GET /v1/models"]},
        detection={"ml": {"enabled": True, "labels": {"person": "PERSON", "city": "LOCATION"}}},
    )
    code, out, _ = _run(capsys, "check", "--config", str(config))
    assert code == 0
    assert "detectors: literal, rules, gliner" in out
    assert "dictionary: no terms" in out
    assert re.search(r"entity types: .*\bLOCATION\b.*\bPERSON\b", out)
    assert "passthrough: anthropic GET /v1/models" in out
    assert "ml: urchade/gliner_multi_pii-v1 at revision 1fcf13e85f4e" in out


def test_check_with_an_invalid_file_exits_2_and_names_every_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(
        tmp_path,
        listen={"port": 70000},
        policy={"unsupported_content": "maybe"},
        detection={"allow_list": "not a list", "colour": "blue"},
    )
    code, out, err = _run(capsys, "check", "--config", str(config))
    assert code == 2
    assert out == ""
    for field in (
        "listen.port",
        "policy.unsupported_content",
        "detection.allow_list",
        "detection.colour",
    ):
        assert re.search(rf"^error: {re.escape(field)}: ", err, re.MULTILINE), field


def test_check_errors_never_repeat_the_invalid_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(
        tmp_path,
        policy={"unsupported_content": BROKEN_TERMS[0]},
        detection={"min_propagation_length": BROKEN_TERMS[1]},
    )
    code, _, err = _run(capsys, "check", "--config", str(config))
    assert code == 2
    assert "policy.unsupported_content" in err
    for term in BROKEN_TERMS:
        assert term not in err


def test_check_with_a_broken_dictionary_exits_2_naming_the_entry_but_not_the_terms(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dictionary = tmp_path / "dictionary.yaml"
    dictionary.write_text(
        "terms:\n"
        f"  - term: {BROKEN_TERMS[0]}\n"
        "    type: PROJECT\n"
        f"  - term: {BROKEN_TERMS[1]}\n"
        "    type: not-a-type\n",
        encoding="utf-8",
    )
    config = _config(tmp_path, detection={"dictionary": {"files": ["dictionary.yaml"]}})
    code, out, err = _run(capsys, "check", "--config", str(config))
    assert code == 2
    assert out == ""
    assert f"error: dictionary file {dictionary}, entry 2: entity type" in err
    for term in BROKEN_TERMS:
        assert term not in err


def test_check_reports_every_broken_dictionary_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The second line of the first file is not valid YAML; the parser would quote it.
    (tmp_path / "first.yaml").write_text(
        f"terms:\n  - term: {BROKEN_TERMS[0]}: [\n", encoding="utf-8"
    )
    (tmp_path / "second.yaml").write_text(f"- {BROKEN_TERMS[1]}\n", encoding="utf-8")
    config = _config(
        tmp_path,
        detection={"dictionary": {"files": ["first.yaml", "second.yaml", "missing.yaml"]}},
    )
    code, _, err = _run(capsys, "check", "--config", str(config))
    assert code == 2
    assert "first.yaml is not valid YAML at line 2" in err
    assert "second.yaml must be a mapping with a single 'terms' key" in err
    assert re.search(r"error: cannot read .*missing\.yaml: ", err)
    for term in BROKEN_TERMS:
        assert term not in err


def test_check_with_conflicting_terms_across_files_exits_2_without_the_terms(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path / "a.yaml", {"terms": [{"term": BROKEN_TERMS[0], "type": "PROJECT"}]})
    _write(tmp_path / "b.yaml", {"terms": [{"term": BROKEN_TERMS[0], "type": "ORG"}]})
    config = _config(tmp_path, detection={"dictionary": {"files": ["a.yaml", "b.yaml"]}})
    code, _, err = _run(capsys, "check", "--config", str(config))
    assert code == 2
    assert "same term with different entity types" in err
    assert BROKEN_TERMS[0] not in err


def test_check_with_invalid_yaml_exits_2_without_quoting_the_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "aliasgate.yaml"
    config.write_text(f"detection:\n  allow_list: [{BROKEN_TERMS[0]}\n", encoding="utf-8")
    code, _, err = _run(capsys, "check", "--config", str(config))
    assert code == 2
    assert f"error: the configuration file {config} is not valid YAML at line " in err
    assert BROKEN_TERMS[0] not in err


def test_check_with_a_configuration_that_is_not_a_mapping_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "aliasgate.yaml"
    config.write_text(f"- {BROKEN_TERMS[0]}\n", encoding="utf-8")
    code, _, err = _run(capsys, "check", "--config", str(config))
    assert code == 2
    assert "error: (top level): " in err
    assert BROKEN_TERMS[0] not in err


def test_check_with_a_missing_configuration_file_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.yaml"
    code, _, err = _run(capsys, "check", "--config", str(missing))
    assert code == 2
    assert f"error: cannot read {missing}: " in err


def test_check_with_an_invalid_environment_variable_exits_2_naming_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALIASGATE_LISTEN__PORT", "0")
    code, _, err = _run(capsys, "check", "--config", str(_config(tmp_path)))
    assert code == 2
    assert "error: listen.port: " in err


def test_check_with_an_environment_variable_that_does_not_parse_exits_2_without_its_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALIASGATE_DETECTION__ALLOW_LIST", f'["{BROKEN_TERMS[0]}"')
    code, _, err = _run(capsys, "check", "--config", str(_config(tmp_path)))
    assert code == 2
    assert 'field "detection"' in err
    assert BROKEN_TERMS[0] not in err


def test_check_with_ml_enabled_without_the_extra_exits_2_with_the_install_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "gliner", None)  # makes `import gliner` fail
    config = _config(tmp_path, detection={"ml": {"enabled": True}})
    code, _, err = _run(capsys, "check", "--config", str(config))
    assert code == 2
    assert "pip install 'aliasgate[ml]'" in err


def test_an_unexpected_failure_exits_1_naming_only_the_exception_class(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(settings: object) -> Pseudonymizer:
        raise KeyError(BROKEN_TERMS[0])

    monkeypatch.setattr(cli, "build_pseudonymizer", broken)
    code, out, err = _run(capsys, "check", "--config", str(_config(tmp_path)))
    assert code == 1
    assert out == ""
    assert err == "error: unexpected failure (KeyError)\n"


# demo


def test_demo_prints_placeholders_on_stdout_and_counts_on_stderr_without_surfaces(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _dictionary_config(tmp_path, ("Juan Pérez", "PERSON"))
    code, out, err = _demo(capsys, monkeypatch, DEMO_TEXT + "\n", "--config", str(config))
    assert code == 0
    assert out == "Contact [PERSON_1] at [EMAIL_1]\n"
    assert re.search(r"^EMAIL +1 +1$", err, re.MULTILINE)
    assert re.search(r"^PERSON +1 +1$", err, re.MULTILINE)
    assert "Juan Pérez" not in err
    assert "juan.perez@example.com" not in err


def test_demo_counts_entities_and_occurrences_separately(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _dictionary_config(tmp_path, ("Juan Pérez", "PERSON"))
    text = "Juan Pérez and JUAN PÉREZ wrote to juan.perez@example.com"
    code, out, err = _demo(capsys, monkeypatch, text, "--config", str(config))
    assert code == 0
    assert out == "[PERSON_1] and [PERSON_1] wrote to [EMAIL_1]"
    assert re.search(r"^PERSON +1 +2$", err, re.MULTILINE)


def test_demo_with_show_mapping_prints_the_pairs_on_stderr_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _dictionary_config(tmp_path, ("Juan Pérez", "PERSON"))
    code, out, err = _demo(
        capsys, monkeypatch, DEMO_TEXT, "--config", str(config), "--show-mapping"
    )
    assert code == 0
    assert out == "Contact [PERSON_1] at [EMAIL_1]"
    assert '[PERSON_1]  "Juan Pérez"' in err
    assert '[EMAIL_1]  "juan.perez@example.com"' in err


def test_demo_with_nothing_detected_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, out, err = _demo(
        capsys, monkeypatch, "Nothing to see", "--config", str(_config(tmp_path))
    )
    assert code == 0
    assert out == "Nothing to see"
    assert err == "no entities detected\n"


def test_demo_with_a_failing_detector_exits_1_without_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(self: RuleDetector, text: str) -> list[object]:
        raise ValueError(text)  # the engine reports the detector, never the message

    monkeypatch.setattr(RuleDetector, "detect", failing)
    code, out, err = _demo(capsys, monkeypatch, DEMO_TEXT, "--config", str(_config(tmp_path)))
    assert code == 1
    assert out == ""
    assert err == "error: detector 'rules' raised an exception\n"
    assert "Juan Pérez" not in err


def test_demo_with_an_invalid_configuration_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, listen={"port": 0})
    code, out, err = _demo(capsys, monkeypatch, DEMO_TEXT, "--config", str(config))
    assert code == 2
    assert out == ""
    assert "error: listen.port: " in err


# serve


def test_serve_runs_uvicorn_with_the_host_and_port_given(
    tmp_path: Path, served: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(
        tmp_path, listen={"host": "127.0.0.1", "port": 9123}, logging={"level": "DEBUG"}
    )
    code, _, err = _run(capsys, "serve", "--config", str(config), "--host", "::1", "--port", "9200")
    assert code == 0
    assert isinstance(served["app"], Starlette)
    assert served["run"] == {"host": "::1", "port": 9200, "log_config": None, "access_log": False}
    assert served["level"] == "DEBUG"
    assert WARNING not in err


def test_serve_without_overrides_uses_the_configured_address(
    tmp_path: Path, served: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path, listen={"port": 9123})
    code, _, err = _run(capsys, "serve", "--config", str(config))
    assert code == 0
    assert served["run"]["host"] == "127.0.0.1"
    assert served["run"]["port"] == 9123
    assert served["level"] == "INFO"
    assert err == ""


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "localhost", "LOCALHOST"])
def test_serve_on_a_loopback_host_prints_no_warning(
    host: str, tmp_path: Path, served: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = _run(capsys, "serve", "--config", str(_config(tmp_path)), "--host", host)
    assert code == 0
    assert err == ""


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "192.0.2.44", "[::1]", "gateway.example.com"],  # noqa: S104
)
def test_serve_on_a_non_loopback_host_prints_one_warning_line(
    host: str, tmp_path: Path, served: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = _run(capsys, "serve", "--config", str(_config(tmp_path)), "--host", host)
    assert code == 0
    assert served["run"]["host"] == host
    assert err.count("\n") == 1
    assert err.startswith("warning: ")
    assert WARNING in err
    assert "no client authentication" in err


def test_serve_warns_about_a_non_loopback_host_from_the_configuration(
    tmp_path: Path, served: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path, listen={"host": "0.0.0.0"})  # noqa: S104
    code, _, err = _run(capsys, "serve", "--config", str(config))
    assert code == 0
    assert WARNING in err


@pytest.mark.parametrize("port", ["0", "70000"])
def test_serve_with_an_invalid_port_exits_2_naming_the_field(
    port: str, tmp_path: Path, served: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = _run(capsys, "serve", "--config", str(_config(tmp_path)), "--port", port)
    assert code == 2
    assert "error: listen.port: " in err
    assert "run" not in served


def test_serve_with_a_port_that_is_not_a_number_is_a_usage_error(
    tmp_path: Path, served: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = _run(capsys, "serve", "--config", str(_config(tmp_path)), "--port", "http")
    assert code == 2
    assert "--port" in err
    assert "run" not in served


def test_serve_with_a_broken_dictionary_exits_2_before_serving(
    tmp_path: Path, served: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    config = _dictionary_config(tmp_path, (BROKEN_TERMS[0], "not-a-type"))
    code, _, err = _run(capsys, "serve", "--config", str(config))
    assert code == 2
    assert "entry 1" in err
    assert BROKEN_TERMS[0] not in err
    assert "run" not in served


class _NoEntities:
    """A GLiNER stand-in that finds nothing."""

    def predict_entities(
        self, text: str, labels: list[str], threshold: float
    ) -> list[dict[str, object]]:
        return []

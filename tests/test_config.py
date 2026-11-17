from pathlib import Path

import pytest
from pydantic import ValidationError

from aliasgate.config import Settings, load_settings, load_terms
from aliasgate.detectors.dictionary import Term
from aliasgate.detectors.rules import DEFAULT_RULE_TYPES

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

DEFAULTS = {
    "listen": {"host": "127.0.0.1", "port": 8080, "max_request_bytes": 10_485_760},
    "upstreams": {
        "openai": {"base_url": "https://api.openai.com"},
        "anthropic": {"base_url": "https://api.anthropic.com"},
        "timeout_seconds": 600,
        "passthrough_paths": (),
    },
    "detection": {
        "rules": {
            "enabled_types": (
                "EMAIL",
                "PHONE",
                "IBAN",
                "CREDIT_CARD",
                "NATIONAL_ID",
                "TAX_ID",
                "IP_ADDRESS",
                "SECRET",
            ),
            "phone_regions": ("ES", "US"),
        },
        "dictionary": {"files": ()},
        "allow_list": (),
        "ml": {"enabled": False, "model": None, "threshold": 0.4, "labels": {}},
        "propagate": True,
        "min_propagation_length": 3,
    },
    "policy": {
        "unsupported_content": "block",
        "strip_user_identifiers": True,
        "tool_definitions": True,
    },
    "logging": {"level": "INFO"},
}


def _config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "aliasgate.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_defaults_without_file_or_environment() -> None:
    settings = load_settings(None, env={})
    assert settings.model_dump() == DEFAULTS
    assert set(settings.detection.rules.enabled_types) == DEFAULT_RULE_TYPES


def test_constructing_settings_directly_gives_defaults_and_ignores_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALIASGATE_LISTEN__PORT", "9999")
    assert Settings().model_dump() == DEFAULTS
    assert load_settings(None).listen.port == 9999


def test_yaml_file_values_are_honoured_and_the_rest_keeps_its_defaults(tmp_path: Path) -> None:
    settings = load_settings(_config(tmp_path, "listen:\n  port: 9001\n"), env={})
    assert settings.listen.port == 9001
    assert settings.listen.host == "127.0.0.1"


def test_environment_overrides_the_file_key_by_key(tmp_path: Path) -> None:
    path = _config(tmp_path, "listen:\n  host: 192.0.2.10\n  port: 9001\n")
    settings = load_settings(path, env={"ALIASGATE_LISTEN__PORT": "9002"})
    assert (settings.listen.host, settings.listen.port) == ("192.0.2.10", 9002)


def test_environment_lists_and_mappings_are_json(tmp_path: Path) -> None:
    env = {
        "ALIASGATE_DETECTION__ALLOW_LIST": '["ACME Corporation", "Globex S.L."]',
        "ALIASGATE_DETECTION__ML__LABELS": '{"person": "PERSON"}',
        "aliasgate_policy__unsupported_content": "pass",
    }
    settings = load_settings(None, env=env)
    assert settings.detection.allow_list == ("ACME Corporation", "Globex S.L.")
    assert settings.detection.ml.labels == {"person": "PERSON"}
    assert settings.policy.unsupported_content == "pass"


def test_unrelated_environment_variables_are_ignored() -> None:
    assert load_settings(None, env={"HOME": "/home/example", "PORT": "1"}).model_dump() == DEFAULTS


@pytest.mark.parametrize(
    "content",
    [
        "listn:\n  port: 1\n",
        "listen:\n  prot: 1\n",
        "upstreams:\n  openai:\n    baseurl: https://api.openai.com\n",
        "upstreams:\n  azure:\n    base_url: https://example.com\n",
        "detection:\n  rules:\n    enabled: [EMAIL]\n",
        "detection:\n  dictionary:\n    file: [a.yaml]\n",
        "detection:\n  ml:\n    modle: x\n",
        "detection:\n  allowlist: []\n",
        "policy:\n  unsuported_content: pass\n",
        "logging:\n  lvl: DEBUG\n",
    ],
)
def test_unknown_key_in_any_section_is_rejected(tmp_path: Path, content: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        load_settings(_config(tmp_path, content), env={})


@pytest.mark.parametrize(
    "name",
    ["ALIASGATE_LISTN__PORT", "ALIASGATE_LISTEN__PROT", "ALIASGATE_LISTEN_PORT", "ALIASGATE_X"],
)
def test_unknown_aliasgate_environment_variable_is_rejected(name: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        load_settings(None, env={name: "1"})


@pytest.mark.parametrize(
    "content",
    [
        "upstreams:\n  openai:\n    base_url: ftp://api.example.com\n",
        "upstreams:\n  openai:\n    base_url: https://api.example.com/v1\n",
        "upstreams:\n  openai:\n    base_url: https://api.example.com/?x=1\n",
        "upstreams:\n  openai:\n    base_url: https://user:pass@api.example.com\n",
        "upstreams:\n  openai:\n    base_url: https://\n",
        "upstreams:\n  passthrough_paths: [PATCH /v1/models]\n",
        "upstreams:\n  passthrough_paths: [GET v1/models]\n",
        "upstreams:\n  timeout_seconds: 0\n",
        "listen:\n  port: 0\n",
        "listen:\n  max_request_bytes: 0\n",
        "detection:\n  rules:\n    enabled_types: [email]\n",
        "detection:\n  rules:\n    phone_regions: [ESP]\n",
        "detection:\n  rules:\n    phone_regions: [es]\n",
        "detection:\n  ml:\n    threshold: 1.5\n",
        "detection:\n  ml:\n    labels: {person: person}\n",
        "detection:\n  min_propagation_length: 0\n",
        "policy:\n  unsupported_content: maybe\n",
        "logging:\n  level: LOUD\n",
    ],
    ids=[
        "url-scheme",
        "url-path",
        "url-query",
        "url-credentials",
        "url-without-host",
        "passthrough-method",
        "passthrough-path",
        "timeout-zero",
        "port-zero",
        "max-request-bytes-zero",
        "lower-case-entity-type",
        "three-letter-region",
        "lower-case-region",
        "threshold-above-one",
        "lower-case-label-type",
        "min-propagation-zero",
        "unsupported-content-maybe",
        "log-level",
    ],
)
def test_invalid_values_are_rejected(tmp_path: Path, content: str) -> None:
    with pytest.raises(ValidationError):
        load_settings(_config(tmp_path, content), env={})


def test_validation_errors_do_not_repeat_the_input_value(tmp_path: Path) -> None:
    content = "upstreams:\n  openai:\n    base_url: ftp://internal-host.example.org\n"
    with pytest.raises(ValidationError) as excinfo:
        load_settings(_config(tmp_path, content), env={"ALIASGATE_API_KEY": "sk-" + "x" * 24})
    message = str(excinfo.value)
    assert "internal-host" not in message
    assert "x" * 24 not in message


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://api.example.com/", "https://api.example.com"),
        ("http://localhost:4000", "http://localhost:4000"),
        ("HTTPS://api.example.com", "HTTPS://api.example.com"),
    ],
)
def test_valid_base_urls_are_kept_without_a_trailing_slash(value: str, expected: str) -> None:
    settings = load_settings(None, env={"ALIASGATE_UPSTREAMS__OPENAI__BASE_URL": value})
    assert settings.upstreams.openai.base_url == expected


def test_valid_passthrough_paths_are_accepted() -> None:
    env = {"ALIASGATE_UPSTREAMS__PASSTHROUGH_PATHS": '["GET /v1/models", "DELETE /v1/files/x"]'}
    assert load_settings(None, env=env).upstreams.passthrough_paths == (
        "GET /v1/models",
        "DELETE /v1/files/x",
    )


def test_relative_dictionary_paths_are_resolved_against_the_config_directory(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "conf"
    config_dir.mkdir()
    absolute = tmp_path / "elsewhere" / "terms.yaml"
    path = _config(config_dir, f"detection:\n  dictionary:\n    files: [terms.yaml, {absolute}]\n")
    assert load_settings(path, env={}).detection.dictionary.files == (
        config_dir / "terms.yaml",
        absolute,
    )


def test_dictionary_paths_from_the_environment_follow_the_same_rule(tmp_path: Path) -> None:
    path = _config(tmp_path, "")
    env = {"ALIASGATE_DETECTION__DICTIONARY__FILES": '["terms.yaml"]'}
    assert load_settings(path, env=env).detection.dictionary.files == (tmp_path / "terms.yaml",)


def test_without_a_config_file_relative_paths_use_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    env = {"ALIASGATE_DETECTION__DICTIONARY__FILES": '["terms.yaml"]'}
    assert load_settings(None, env=env).detection.dictionary.files == (tmp_path / "terms.yaml",)


def test_missing_config_file_raises_with_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "nowhere.yaml"
    with pytest.raises(FileNotFoundError, match=r"nowhere\.yaml"):
        load_settings(missing, env={})


def test_empty_config_file_gives_defaults(tmp_path: Path) -> None:
    assert load_settings(_config(tmp_path, ""), env={}).model_dump() == DEFAULTS


def test_config_file_that_is_not_a_mapping_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_settings(_config(tmp_path, "- listen\n"), env={})


def test_settings_are_immutable() -> None:
    settings = load_settings(None, env={})
    with pytest.raises(ValidationError):
        settings.listen.port = 1


def test_example_config_loads_and_points_at_the_example_dictionary() -> None:
    settings = load_settings(EXAMPLES / "aliasgate.example.yaml", env={})
    assert settings.detection.dictionary.files == (EXAMPLES / "dictionary.example.yaml",)
    assert settings.detection.dictionary.files[0].is_file()


# Dictionary files -----------------------------------------------------------------------------


def _file(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "dictionary.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_example_dictionary_has_three_terms() -> None:
    terms = load_terms(EXAMPLES / "dictionary.example.yaml")
    assert len(terms) == 3
    assert all(isinstance(term, Term) for term in terms)


def test_load_terms_reads_terms_in_order(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        "terms:\n"
        "  - term: ACME Corporation\n    type: ORG\n"
        "  - term: Project Falcon\n    type: PROJECT\n",
    )
    assert load_terms(path) == [Term("ACME Corporation", "ORG"), Term("Project Falcon", "PROJECT")]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("terms:\n  - term: ACME Corporation\n    type: ORG\n  - term: Globex S.L.\n", "entry 2"),
        ("terms:\n  - type: ORG\n", "entry 1"),
        ("terms:\n  - term: Globex S.L.\n    type: org\n", "entry 1"),
        ("terms:\n  - term: ACME\n    type: ORG\n  - term: '   '\n    type: ORG\n", "entry 2"),
        ("terms:\n  - term: NO\n    type: ORG\n", "entry 1"),
        ("terms:\n  - term: Globex S.L.\n    type: ORG\n    note: x\n", "entry 1"),
        ("terms:\n  - Globex S.L.\n", "entry 1"),
        ("terms: Globex S.L.\n", "list"),
        ("- term: Globex S.L.\n", "terms"),
        ("words: []\n", "terms"),
    ],
    ids=[
        "missing-type",
        "missing-term",
        "invalid-type",
        "empty-term",
        "yaml-boolean-term",
        "unknown-key",
        "entry-not-a-mapping",
        "terms-not-a-list",
        "top-level-list",
        "no-terms-key",
    ],
)
def test_invalid_dictionary_files_raise_with_the_position_not_the_term(
    tmp_path: Path, content: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message) as excinfo:
        load_terms(_file(tmp_path, content))
    assert "Globex" not in str(excinfo.value)


def test_yaml_syntax_error_reports_the_position_without_the_content(tmp_path: Path) -> None:
    path = _file(tmp_path, "terms:\n  - term: Globex S.L.\n    type: [ORG\n")
    with pytest.raises(ValueError, match="line") as excinfo:
        load_terms(path)
    assert "Globex" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__


def test_empty_terms_list_is_allowed(tmp_path: Path) -> None:
    assert load_terms(_file(tmp_path, "terms:\n")) == []
    assert load_terms(_file(tmp_path, "terms: []\n")) == []


def test_missing_dictionary_file_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_terms(tmp_path / "missing.yaml")

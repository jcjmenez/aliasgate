from pathlib import Path

import pytest

from aliasgate.config import Settings, load_settings
from aliasgate.engine import restore
from aliasgate.factory import build_pseudonymizer

IBAN = "ES91 2100 0418 4502 0005 1332"
IBAN_ACROSS_LINES = IBAN.replace(" ", "\n")


def _settings(tmp_path: Path, config: str = "", **dictionaries: str) -> Settings:
    for name, content in dictionaries.items():
        (tmp_path / f"{name}.yaml").write_text(content, encoding="utf-8")
    if dictionaries:
        files = ", ".join(f"{name}.yaml" for name in dictionaries)
        config = f"detection:\n  dictionary:\n    files: [{files}]\n" + config
    path = tmp_path / "aliasgate.yaml"
    path.write_text(config, encoding="utf-8")
    return load_settings(path, env={})


def _terms(*entries: tuple[str, str]) -> str:
    return "terms:\n" + "".join(f"  - term: {term}\n    type: {kind}\n" for term, kind in entries)


def test_dictionary_and_rules_work_together_and_restore_canonical_forms(tmp_path: Path) -> None:
    settings = _settings(tmp_path, people=_terms(("Juan Pérez", "PERSON")))
    batch = build_pseudonymizer(settings).pseudonymize(
        ["Juan Pérez <juan.perez@example.com>", "juan pérez again"]
    )
    assert batch.texts == ("[PERSON_1] <[EMAIL_1]>", "[PERSON_1] again")
    assert [restore(text, batch.mapping) for text in batch.texts] == [
        "Juan Pérez <juan.perez@example.com>",
        "Juan Pérez again",
    ]


def test_dictionary_beats_rules_on_overlap(tmp_path: Path) -> None:
    settings = _settings(tmp_path, people=_terms(("juan.perez@example.com", "PERSON")))
    batch = build_pseudonymizer(settings).pseudonymize(["Mail juan.perez@example.com now"])
    assert batch.texts == ("Mail [PERSON_1] now",)


def test_allow_list_from_settings_is_applied(tmp_path: Path) -> None:
    config = "detection:\n  allow_list: [juan.perez@example.com, ACME Corporation]\n"
    settings = _settings(tmp_path, config, orgs=_terms(("ACME Corporation", "ORG")))
    batch = build_pseudonymizer(settings).pseudonymize(
        ["juan.perez@example.com, a.b+tag@mail.example.org, ACME Corporation"]
    )
    assert batch.texts == ("juan.perez@example.com, [EMAIL_1], ACME Corporation",)


def test_terms_from_several_dictionary_files_are_combined(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        orgs=_terms(("ACME Corporation", "ORG")),
        projects=_terms(("Project Falcon", "PROJECT")),
    )
    batch = build_pseudonymizer(settings).pseudonymize(["ACME Corporation runs Project Falcon"])
    assert batch.texts == ("[ORG_1] runs [PROJECT_1]",)


def test_conflicting_types_across_dictionary_files_raise(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        orgs=_terms(("Globex S.L.", "ORG")),
        people=_terms(("globex s.l.", "PERSON")),
    )
    with pytest.raises(ValueError, match="different entity types") as excinfo:
        build_pseudonymizer(settings)
    assert "lobex" not in str(excinfo.value)


def test_missing_dictionary_file_fails_at_build_time(tmp_path: Path) -> None:
    settings = load_settings(
        None, env={"ALIASGATE_DETECTION__DICTIONARY__FILES": f'["{tmp_path / "gone.yaml"}"]'}
    )
    with pytest.raises(FileNotFoundError):
        build_pseudonymizer(settings)


def test_rule_settings_are_passed_through(tmp_path: Path) -> None:
    config = "detection:\n  rules:\n    enabled_types: [EMAIL]\n    phone_regions: []\n"
    batch = build_pseudonymizer(_settings(tmp_path, config)).pseudonymize(
        [f"juan.perez@example.com +34 612 345 678 {IBAN}"]
    )
    assert batch.texts == (f"[EMAIL_1] +34 612 345 678 {IBAN}",)


def test_unsupported_phone_region_fails_at_build_time(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "detection:\n  rules:\n    phone_regions: [XX]\n")
    with pytest.raises(ValueError, match="region"):
        build_pseudonymizer(settings)


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ("", ("[IBAN_1]", "[IBAN_1]")),
        ("detection:\n  propagate: false\n", ("[IBAN_1]", IBAN_ACROSS_LINES)),
        ("detection:\n  min_propagation_length: 100\n", ("[IBAN_1]", IBAN_ACROSS_LINES)),
    ],
    ids=["default", "propagate-off", "long-minimum"],
)
def test_propagation_settings_are_passed_through(
    tmp_path: Path, config: str, expected: tuple[str, str]
) -> None:
    batch = build_pseudonymizer(_settings(tmp_path, config)).pseudonymize([IBAN, IBAN_ACROSS_LINES])
    assert batch.texts == expected


def test_ml_enabled_raises_not_implemented(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "detection:\n  ml:\n    enabled: true\n")
    with pytest.raises(NotImplementedError, match="slice 09"):
        build_pseudonymizer(settings)


def test_example_configuration_builds(tmp_path: Path) -> None:
    examples = Path(__file__).resolve().parent.parent / "examples"
    engine = build_pseudonymizer(load_settings(examples / "aliasgate.example.yaml", env={}))
    assert engine.pseudonymize(["ACME Corporation"]).texts == ("[ORG_1]",)

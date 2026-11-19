import re
import sys
import types
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from aliasgate import factory
from aliasgate.config import Settings, load_settings
from aliasgate.engine import restore
from aliasgate.factory import build_pseudonymizer

IBAN = "ES91 2100 0418 4502 0005 1332"
MODEL_WORD = re.compile(r"\w+(?:[-_]\w+)*|\S")
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


# The ml section alone, for configurations that _settings prefixes with a detection section.
ML_SECTION = "  ml:\n    enabled: true\n"
ML_ON = "detection:\n" + ML_SECTION


class FakeEntityModel:
    """Finds the given surfaces in every window and records each call."""

    def __init__(self, found: dict[str, str]) -> None:
        self.found = found
        self.calls: list[str] = []

    def predict_entities(
        self, text: str, labels: list[str], threshold: float
    ) -> list[dict[str, object]]:
        self.calls.append(text)
        return [
            {"start": match.start(), "end": match.end(), "label": label, "score": 0.7}
            for surface, label in self.found.items()
            for match in re.finditer(re.escape(surface), text)
        ]


@dataclass
class Loaded:
    """The fake model handed to the factory, and the (model id, revision) pairs it loaded."""

    model: FakeEntityModel
    calls: list[tuple[str, str | None]] = field(default_factory=list)


@pytest.fixture
def loaded(monkeypatch: pytest.MonkeyPatch) -> Loaded:
    """Replace model loading with a FakeEntityModel."""
    result = Loaded(
        FakeEntityModel(
            {
                "Alice Johnson": "person",
                "juan.perez@example.com": "person",
                "Globex S.L. Madrid": "address",
            }
        )
    )

    def fake_load(model_id: str, revision: str | None) -> FakeEntityModel:
        result.calls.append((model_id, revision))
        return result.model

    monkeypatch.setattr(factory, "load_gliner", fake_load)
    return result


def test_ml_enabled_without_the_extra_fails_at_build_time_naming_the_install_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "gliner", None)  # makes `import gliner` fail
    with pytest.raises(RuntimeError, match=re.escape("pip install 'aliasgate[ml]'")):
        build_pseudonymizer(_settings(tmp_path, ML_ON))


def test_ml_disabled_loads_no_model(tmp_path: Path, loaded: Loaded) -> None:
    batch = build_pseudonymizer(_settings(tmp_path)).pseudonymize(["Alice Johnson"])
    assert batch.texts == ("Alice Johnson",)
    assert loaded.calls == []


def test_ml_finds_what_rules_and_dictionary_do_not(tmp_path: Path, loaded: Loaded) -> None:
    settings = _settings(tmp_path, ML_ON)
    batch = build_pseudonymizer(settings).pseudonymize(["Alice Johnson wrote"])
    assert batch.texts == ("[PERSON_1] wrote",)
    assert loaded.calls == [(settings.detection.ml.model, settings.detection.ml.revision)]


def test_dictionary_and_rules_beat_ml_on_overlapping_spans(tmp_path: Path, loaded: Loaded) -> None:
    settings = _settings(tmp_path, ML_SECTION, orgs=_terms(("Globex S.L.", "ORG")))
    batch = build_pseudonymizer(settings).pseudonymize(
        ["Mail juan.perez@example.com at Globex S.L. Madrid"]
    )
    assert batch.texts == ("Mail [EMAIL_1] at [ORG_1] Madrid",)


def test_build_runs_a_full_window_warm_up_prediction(tmp_path: Path, loaded: Loaded) -> None:
    config = ML_ON + "    window_words: 40\n    overlap_words: 10\n"
    build_pseudonymizer(_settings(tmp_path, config))
    assert len(loaded.model.calls) == 1
    assert len(MODEL_WORD.findall(loaded.model.calls[0])) == 40


def test_ml_results_are_cached_across_requests(tmp_path: Path, loaded: Loaded) -> None:
    engine = build_pseudonymizer(_settings(tmp_path, ML_ON))
    for _ in range(3):
        engine.pseudonymize(["Alice Johnson wrote", "Alice Johnson replied"])
    assert loaded.model.calls[1:] == ["Alice Johnson wrote", "Alice Johnson replied"]


def test_cache_entries_zero_disables_the_cache(tmp_path: Path, loaded: Loaded) -> None:
    engine = build_pseudonymizer(_settings(tmp_path, ML_ON + "    cache_entries: 0\n"))
    engine.pseudonymize(["Alice Johnson wrote"])
    engine.pseudonymize(["Alice Johnson wrote"])
    assert loaded.model.calls[1:] == ["Alice Johnson wrote", "Alice Johnson wrote"]


def test_ml_settings_reach_the_model(
    tmp_path: Path, loaded: Loaded, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[list[str], float]] = []
    original = FakeEntityModel.predict_entities

    def recording(
        self: FakeEntityModel, text: str, labels: list[str], threshold: float
    ) -> list[dict[str, object]]:
        seen.append((labels, threshold))
        return original(self, text, labels, threshold)

    monkeypatch.setattr(FakeEntityModel, "predict_entities", recording)
    config = (
        ML_ON
        + "    model: example-org/other-model\n    revision: null\n    threshold: 0.6\n"
        + "    labels: {person: PERSON, company: ORG}\n"
    )
    build_pseudonymizer(_settings(tmp_path, config)).pseudonymize(["Alice Johnson"])
    assert loaded.calls == [("example-org/other-model", None)]
    assert seen == [(["person", "company"], 0.6)] * 2


def test_a_model_that_fails_its_warm_up_fails_the_build(
    tmp_path: Path, loaded: Loaded, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(
        self: FakeEntityModel, text: str, labels: list[str], threshold: float
    ) -> list[dict[str, object]]:
        raise RuntimeError("broken model")

    monkeypatch.setattr(FakeEntityModel, "predict_entities", broken)
    with pytest.raises(RuntimeError, match="broken model"):
        build_pseudonymizer(_settings(tmp_path, ML_ON))


def test_a_window_longer_than_the_model_accepts_fails_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Splitter:
        def __call__(self, text: str) -> Iterator[tuple[str, int, int]]:
            for match in MODEL_WORD.finditer(text):
                yield match.group(), match.start(), match.end()

    class SmallGliner:
        config = types.SimpleNamespace(max_len=100)
        data_processor = types.SimpleNamespace(words_splitter=Splitter())

        @classmethod
        def from_pretrained(cls, model_id: str, revision: str | None = None) -> "SmallGliner":
            return cls()

        def predict_entities(
            self, text: str, labels: list[str], flat_ner: bool = True, threshold: float = 0.5
        ) -> list[dict[str, object]]:
            return []

    module = types.ModuleType("gliner")
    module.GLiNER = SmallGliner  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gliner", module)
    with pytest.raises(ValueError, match="limit of 100"):
        build_pseudonymizer(_settings(tmp_path, ML_ON))  # default window: 250 words
    build_pseudonymizer(_settings(tmp_path, ML_ON + "    window_words: 100\n"))


def test_example_configuration_builds(tmp_path: Path) -> None:
    examples = Path(__file__).resolve().parent.parent / "examples"
    engine = build_pseudonymizer(load_settings(examples / "aliasgate.example.yaml", env={}))
    assert engine.pseudonymize(["ACME Corporation"]).texts == ("[ORG_1]",)

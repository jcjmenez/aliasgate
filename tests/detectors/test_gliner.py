import re
import subprocess
import sys
import threading
import time
import types
from collections.abc import Callable, Iterator
from typing import Any, ClassVar

import pytest

from aliasgate.detectors.gliner import GlinerDetector, load_gliner
from aliasgate.engine import DetectionError, Pseudonymizer
from aliasgate.types import PRIORITY_ML, Span

LABELS = {"person": "PERSON", "organization": "ORG"}
MODEL_ID = "example-org/gliner-test"
REVISION = "0123456789abcdef0123456789abcdef01234567"

type Result = dict[str, object]


def _found(text: str, surface: str, label: str, score: float = 0.9) -> list[Result]:
    return [
        {"start": match.start(), "end": match.end(), "text": surface, "label": label,
         "score": score}
        for match in re.finditer(re.escape(surface), text)
    ]  # fmt: skip


class FakeModel:
    """An EntityModel that answers with `respond(window_text)` and records every call."""

    def __init__(self, respond: Callable[[str], list[Result]]) -> None:
        self.respond = respond
        self.calls: list[tuple[str, list[str], float]] = []

    def predict_entities(self, text: str, labels: list[str], threshold: float) -> list[Result]:
        self.calls.append((text, labels, threshold))
        return self.respond(text)


def _detector(
    model: FakeModel, *, window_words: int = 250, overlap_words: int = 50, **overrides: Any
) -> GlinerDetector:
    settings: dict[str, Any] = {
        "model_id": MODEL_ID,
        "revision": REVISION,
        "labels": LABELS,
        "threshold": 0.4,
    }
    settings.update(overrides)
    return GlinerDetector(model, window_words=window_words, overlap_words=overlap_words, **settings)


def _span(text: str, surface: str, entity_type: str, score: float = 0.9) -> Span:
    start = text.index(surface)
    return Span(start, start + len(surface), entity_type, score, "gliner", PRIORITY_ML)


def test_spans_are_mapped_to_entity_types_with_the_ml_priority() -> None:
    text = "Juan Pérez works at ACME Corporation."
    model = FakeModel(
        lambda window: (
            _found(window, "Juan Pérez", "person", 0.95)
            + _found(window, "ACME Corporation", "organization", 0.8)
        )
    )
    assert _detector(model).detect(text) == [
        _span(text, "Juan Pérez", "PERSON", 0.95),
        _span(text, "ACME Corporation", "ORG", 0.8),
    ]
    assert model.calls == [(text, ["person", "organization"], 0.4)]


def test_offsets_are_shifted_for_the_second_window() -> None:
    text = "one two three four five six seven eight nine ten Alice Johnson twelve"
    model = FakeModel(lambda window: _found(window, "Alice Johnson", "person"))
    spans = _detector(model, window_words=8, overlap_words=2).detect(text)
    assert spans == [_span(text, "Alice Johnson", "PERSON")]
    assert [call[0] for call in model.calls] == [
        "one two three four five six seven eight",
        "seven eight nine ten Alice Johnson twelve",
    ]


@pytest.mark.parametrize(("first", "second"), [(0.6, 0.8), (0.8, 0.6)])
def test_an_entity_seen_by_two_windows_appears_once_with_the_higher_score(
    first: float, second: float
) -> None:
    text = "one two three Alice Johnson six seven"  # windows: words 0-4 and 2-6
    scores = iter([first, second])

    def respond(window: str) -> list[Result]:
        return _found(window, "Alice Johnson", "person", next(scores))

    spans = _detector(FakeModel(respond), window_words=5, overlap_words=3).detect(text)
    assert spans == [_span(text, "Alice Johnson", "PERSON", 0.8)]


def test_different_overlapping_spans_are_all_returned() -> None:
    text = "Alice Johnson Smith"
    model = FakeModel(
        lambda window: (
            _found(window, "Alice Johnson", "person", 0.7)
            + _found(window, "Johnson Smith", "organization", 0.6)
            + _found(window, "Alice Johnson", "organization", 0.5)
        )
    )
    # Sorted by (start, end), then entity type, so the order does not depend on the model's.
    assert _detector(model).detect(text) == [
        _span(text, "Alice Johnson", "ORG", 0.5),
        _span(text, "Alice Johnson", "PERSON", 0.7),
        _span(text, "Johnson Smith", "ORG", 0.6),
    ]


def test_unknown_labels_are_ignored() -> None:
    text = "Juan Pérez, juan.perez@example.com"
    model = FakeModel(
        lambda window: (
            _found(window, "Juan Pérez", "person")
            + _found(window, "juan.perez@example.com", "email")
            + [{"start": 0, "end": 4, "text": "Juan", "label": None, "score": 0.9}]
            + [{"start": 0, "end": 4, "text": "Juan", "label": ["person"], "score": 0.9}]
        )
    )
    assert _detector(model).detect(text) == [_span(text, "Juan Pérez", "PERSON")]


@pytest.mark.parametrize("padding", [" ", "  ", "\n", "\t "])
def test_whitespace_around_a_result_is_trimmed(padding: str) -> None:
    text = f"Hello{padding}Juan Pérez{padding}today"
    surface = f"{padding}Juan Pérez{padding}"
    model = FakeModel(lambda window: _found(window, surface, "person"))
    assert _detector(model).detect(text) == [_span(text, "Juan Pérez", "PERSON")]


@pytest.mark.parametrize("surface", [" J ", "J", "  ", "\n"])
def test_a_result_shorter_than_two_characters_after_trimming_is_dropped(surface: str) -> None:
    text = f"Hello{surface}again"
    model = FakeModel(lambda window: _found(window, surface, "person"))
    assert _detector(model).detect(text) == []


@pytest.mark.parametrize(("score", "expected"), [(1.3, 1.0), (-0.2, 0.0), (1, 1.0), (0.5, 0.5)])
def test_scores_are_clamped_to_the_unit_interval(score: float, expected: float) -> None:
    text = "Juan Pérez"
    model = FakeModel(lambda window: _found(window, "Juan Pérez", "person", score))
    assert [span.score for span in _detector(model).detect(text)] == [expected]


def test_results_are_sorted_by_position() -> None:
    text = "Alice Johnson met Juan Pérez"
    model = FakeModel(
        lambda window: (
            _found(window, "Juan Pérez", "person") + _found(window, "Alice Johnson", "person")
        )
    )
    assert _detector(model).detect(text) == [
        _span(text, "Alice Johnson", "PERSON"),
        _span(text, "Juan Pérez", "PERSON"),
    ]


@pytest.mark.parametrize(
    "result",
    [
        {"start": 0, "end": 99, "label": "person", "score": 0.9},
        {"start": -1, "end": 4, "label": "person", "score": 0.9},
        {"start": 4, "end": 2, "label": "person", "score": 0.9},
        {"start": "0", "end": 4, "label": "person", "score": 0.9},
        {"start": 0, "end": 4.0, "label": "person", "score": 0.9},
        {"start": False, "end": 4, "label": "person", "score": 0.9},
        {"end": 4, "label": "person", "score": 0.9},
        {"start": 0, "end": 4, "label": "person", "score": "high"},
        {"start": 0, "end": 4, "label": "person", "score": True},
        {"start": 0, "end": 4, "label": "person", "score": float("nan")},
        {"start": 0, "end": 4, "label": "person"},
        ["not", "a", "mapping"],
    ],
    ids=[
        "end-outside-window",
        "negative-start",
        "end-before-start",
        "string-offset",
        "float-offset",
        "bool-offset",
        "missing-start",
        "string-score",
        "bool-score",
        "nan-score",
        "missing-score",
        "not-a-mapping",
    ],
)
def test_a_malformed_model_result_raises_without_repeating_text(result: object) -> None:
    text = "Juan Pérez called"
    model = FakeModel(lambda window: [result])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="model result") as excinfo:
        _detector(model).detect(text)
    assert "Juan" not in str(excinfo.value)


def test_the_model_raising_propagates_and_the_engine_fails_closed() -> None:
    def respond(window: str) -> list[Result]:
        raise RuntimeError("inference failed")

    detector = _detector(FakeModel(respond))
    with pytest.raises(RuntimeError, match="inference failed"):
        detector.detect("Juan Pérez")
    with pytest.raises(DetectionError) as excinfo:
        Pseudonymizer([detector]).pseudonymize(["Juan Pérez"])
    assert excinfo.value.detector_name == "gliner"


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_text_without_words_does_not_reach_the_model(text: str) -> None:
    model = FakeModel(lambda window: _found(window, "x", "person"))
    assert _detector(model).detect(text) == []
    assert model.calls == []


def test_the_detector_is_named_gliner() -> None:
    assert _detector(FakeModel(lambda window: [])).name == "gliner"


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"labels": {}}, "labels"),
        ({"labels": {"person": "person"}}, "entity type"),
        ({"labels": {"": "PERSON"}}, "labels"),
        ({"threshold": 1.5}, "threshold"),
        ({"threshold": -0.1}, "threshold"),
        ({"threshold": float("nan")}, "threshold"),
        ({"window_words": 10, "overlap_words": 10}, "overlap_words"),
    ],
    ids=[
        "no-labels",
        "invalid-type",
        "empty-label",
        "threshold-high",
        "threshold-low",
        "threshold-nan",
        "overlap",
    ],
)
def test_invalid_settings_raise_at_construction(overrides: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _detector(FakeModel(lambda window: []), **overrides)


def test_fingerprint_is_stable_and_ignores_label_order() -> None:
    model = FakeModel(lambda window: [])
    first = _detector(model, labels={"person": "PERSON", "organization": "ORG"})
    second = _detector(model, labels={"organization": "ORG", "person": "PERSON"})
    assert first.fingerprint == second.fingerprint
    assert first.fingerprint == _detector(model, labels=LABELS).fingerprint


@pytest.mark.parametrize(
    "overrides",
    [
        {"threshold": 0.5},
        {"labels": {"person": "PERSON"}},
        {"labels": {"person": "PERSON", "organization": "COMPANY"}},
        {"labels": {"person": "PERSON", "company": "ORG"}},
        {"model_id": "example-org/other-model"},
        {"revision": "fedcba9876543210fedcba9876543210fedcba98"},
        {"revision": None},
        {"window_words": 200},
        {"overlap_words": 40},
    ],
    ids=[
        "threshold",
        "fewer-labels",
        "label-type",
        "label-name",
        "model-id",
        "revision",
        "no-revision",
        "window",
        "overlap",
    ],
)
def test_fingerprint_changes_with_the_configuration(overrides: dict[str, Any]) -> None:
    model = FakeModel(lambda window: [])
    assert _detector(model).fingerprint != _detector(model, **overrides).fingerprint


class ConcurrencyProbe:
    """An EntityModel that records how many calls are inside it at once."""

    def __init__(self) -> None:
        self.inside = 0
        self.most_inside = 0
        self.counter_lock = threading.Lock()
        self.first_entered = threading.Event()
        self.second_started = threading.Event()

    def predict_entities(self, text: str, labels: list[str], threshold: float) -> list[Result]:
        with self.counter_lock:
            self.inside += 1
            self.most_inside = max(self.most_inside, self.inside)
        self.first_entered.set()
        # Stay inside long enough for the second thread to arrive, if nothing stops it.
        self.second_started.wait(timeout=5)
        time.sleep(0.1)
        with self.counter_lock:
            self.inside -= 1
        return []


def test_inference_runs_one_call_at_a_time() -> None:
    probe = ConcurrencyProbe()
    detector = GlinerDetector(probe, model_id=MODEL_ID, revision=REVISION, labels=LABELS,
                              threshold=0.4)  # fmt: skip

    def second() -> None:
        probe.second_started.set()
        detector.detect("Alice Johnson")

    first_thread = threading.Thread(target=detector.detect, args=("Juan Pérez",))
    first_thread.start()
    assert probe.first_entered.wait(timeout=5)
    second_thread = threading.Thread(target=second)
    second_thread.start()
    first_thread.join(timeout=10)
    second_thread.join(timeout=10)
    assert probe.most_inside == 1


# load_gliner, with a stand-in for the gliner package: the real one needs torch and a download.


class FakeSplitter:
    """gliner's WhitespaceTokenSplitter: yields (token, start, end)."""

    def __call__(self, text: str) -> Iterator[tuple[str, int, int]]:
        for match in re.finditer(r"\w+(?:[-_]\w+)*|\S", text):
            yield match.group(), match.start(), match.end()


class FakeGliner:
    """The parts of gliner.GLiNER that load_gliner relies on, with the real argument order."""

    loaded: ClassVar[list[tuple[str, str | None]]] = []

    def __init__(self, max_len: object = 384) -> None:
        self.config = types.SimpleNamespace(max_len=max_len)
        self.data_processor = types.SimpleNamespace(words_splitter=FakeSplitter())
        self.calls: list[dict[str, object]] = []

    @classmethod
    def from_pretrained(cls, model_id: str, revision: str | None = None) -> "FakeGliner":
        cls.loaded.append((model_id, revision))
        return cls()

    def predict_entities(
        self, text: str, labels: list[str], flat_ner: bool = True, threshold: float = 0.5
    ) -> list[Result]:
        self.calls.append(
            {"text": text, "labels": labels, "flat_ner": flat_ner, "threshold": threshold}
        )
        return _found(text, "Juan Pérez", "person")


@pytest.fixture
def fake_gliner(monkeypatch: pytest.MonkeyPatch) -> type[FakeGliner]:
    module = types.ModuleType("gliner")
    module.GLiNER = FakeGliner  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gliner", module)
    monkeypatch.setattr(FakeGliner, "loaded", [])
    return FakeGliner


def test_load_gliner_without_the_extra_names_the_install_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "gliner", None)  # makes `import gliner` fail
    with pytest.raises(RuntimeError, match=re.escape("pip install 'aliasgate[ml]'")):
        load_gliner(MODEL_ID, REVISION)


def test_load_gliner_loads_the_given_model_and_revision(fake_gliner: type[FakeGliner]) -> None:
    load_gliner(MODEL_ID, REVISION)
    load_gliner(MODEL_ID, None)
    assert fake_gliner.loaded == [(MODEL_ID, REVISION), (MODEL_ID, None)]


def test_the_loaded_model_passes_the_threshold_by_keyword(
    monkeypatch: pytest.MonkeyPatch, fake_gliner: type[FakeGliner]
) -> None:
    # GLiNER's third positional parameter is flat_ner, not threshold.
    instance = FakeGliner()
    monkeypatch.setattr(FakeGliner, "from_pretrained", classmethod(lambda cls, *a, **k: instance))
    model = load_gliner(MODEL_ID, REVISION)
    assert model.predict_entities("Juan Pérez", ["person"], 0.4) == _found(
        "Juan Pérez", "Juan Pérez", "person"
    )
    assert instance.calls == [
        {"text": "Juan Pérez", "labels": ["person"], "flat_ner": True, "threshold": 0.4}
    ]


def test_the_loaded_model_refuses_a_text_it_would_truncate(
    monkeypatch: pytest.MonkeyPatch, fake_gliner: type[FakeGliner]
) -> None:
    instance = FakeGliner(max_len=7)
    monkeypatch.setattr(FakeGliner, "from_pretrained", classmethod(lambda cls, *a, **k: instance))
    model = load_gliner(MODEL_ID, REVISION)
    model.predict_entities("juan.perez@example.com", ["person"], 0.4)  # 7 model words
    assert len(instance.calls) == 1
    with pytest.raises(ValueError, match="limit of 7") as excinfo:
        model.predict_entities("juan.perez@example.com!", ["person"], 0.4)  # 8 model words
    assert "juan" not in str(excinfo.value)
    assert len(instance.calls) == 1


@pytest.mark.parametrize("max_len", [None, 0, "384", True])
def test_load_gliner_rejects_a_model_whose_input_limit_it_cannot_read(
    monkeypatch: pytest.MonkeyPatch, fake_gliner: type[FakeGliner], max_len: object
) -> None:
    instance = FakeGliner(max_len=max_len)
    monkeypatch.setattr(FakeGliner, "from_pretrained", classmethod(lambda cls, *a, **k: instance))
    with pytest.raises(RuntimeError, match="input limit"):
        load_gliner(MODEL_ID, REVISION)


def test_load_gliner_rejects_a_model_without_a_word_splitter(
    monkeypatch: pytest.MonkeyPatch, fake_gliner: type[FakeGliner]
) -> None:
    instance = FakeGliner()
    del instance.data_processor
    monkeypatch.setattr(FakeGliner, "from_pretrained", classmethod(lambda cls, *a, **k: instance))
    with pytest.raises(RuntimeError, match="input limit"):
        load_gliner(MODEL_ID, REVISION)


def test_importing_aliasgate_imports_neither_gliner_nor_torch() -> None:
    code = (
        "import importlib, pkgutil, sys\n"
        "import aliasgate\n"
        "for module in pkgutil.walk_packages(aliasgate.__path__, 'aliasgate.'):\n"
        "    importlib.import_module(module.name)\n"
        "heavy = {'gliner', 'torch', 'transformers'}\n"
        "print(sorted(name for name in sys.modules if name.split('.')[0] in heavy))\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed code, the test's own interpreter
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    )
    assert result.stdout.strip() == "[]"

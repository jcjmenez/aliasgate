"""Tests with the real model. They download it on first use and are deselected in default CI."""

import pytest

from aliasgate.config import MLSettings
from aliasgate.detectors.gliner import GlinerDetector, load_gliner
from aliasgate.types import Span

pytestmark = pytest.mark.ml


@pytest.fixture(scope="module")
def detector() -> GlinerDetector:
    pytest.importorskip("gliner")
    settings = MLSettings()
    return GlinerDetector(
        load_gliner(settings.model, settings.revision),
        model_id=settings.model,
        revision=settings.revision,
        labels=settings.labels,
        threshold=settings.threshold,
        window_words=settings.window_words,
        overlap_words=settings.overlap_words,
    )


def _found(text: str, spans: list[Span]) -> set[tuple[str, str]]:
    return {(text[span.start : span.end], span.entity_type) for span in spans}


@pytest.mark.parametrize(
    "text",
    [
        "Yesterday Juan Pérez signed the renewal with ACME Corporation in London.",
        "Ayer Juan Pérez firmó la renovación con ACME Corporation en Sevilla.",
    ],
    ids=["english", "spanish"],
)
def test_the_real_model_finds_a_person_and_an_organisation(
    detector: GlinerDetector, text: str
) -> None:
    found = _found(text, detector.detect(text))
    assert ("Juan Pérez", "PERSON") in found
    assert ("ACME Corporation", "ORG") in found


def test_names_after_dense_punctuation_are_still_found(detector: GlinerDetector) -> None:
    # 206 whitespace-separated words but 1,408 model words: a single whitespace window would be
    # truncated by the model and lose the names at the end.
    text = " ".join(["row,1,2,3"] * 200) + " Contact: Alice Johnson at ACME Corporation."
    found = _found(text, detector.detect(text))
    assert ("Alice Johnson", "PERSON") in found
    assert ("ACME Corporation", "ORG") in found


SENTENCE = (
    "The meeting with Alice Johnson from Globex S.L. went well and we agreed to review "
    "the contract next week before sending the final proposal to the purchasing team."
)


def _long_text(words: int) -> str:
    """`words` whitespace-separated words, repeating SENTENCE."""
    sentence = SENTENCE.split(" ")
    return " ".join((sentence * (words // len(sentence) + 1))[:words])


def test_a_3000_word_text_is_processed(detector: GlinerDetector) -> None:
    text = _long_text(3000)
    spans = detector.detect(text)
    assert ("Alice Johnson", "PERSON") in _found(text, spans)
    assert all(0 <= span.start < span.end <= len(text) for span in spans)


def test_detecting_the_same_text_twice_gives_the_same_spans(detector: GlinerDetector) -> None:
    text = _long_text(600)
    assert detector.detect(text) == detector.detect(text)

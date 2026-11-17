import pytest

from aliasgate import types
from aliasgate.types import (
    MAX_ENTITY_TYPE_LENGTH,
    PRIORITY_DICTIONARY,
    PRIORITY_LITERAL,
    PRIORITY_ML,
    PRIORITY_PROPAGATION,
    PRIORITY_RULE,
    Detector,
    Span,
    validate_entity_type,
)

BUILTIN_TYPES = (
    "PERSON",
    "ORG",
    "ADDRESS",
    "EMAIL",
    "PHONE",
    "IBAN",
    "CREDIT_CARD",
    "NATIONAL_ID",
    "TAX_ID",
    "IP_ADDRESS",
    "URL",
    "SECRET",
    "LITERAL",
)


def _span(**overrides: object) -> Span:
    fields: dict[str, object] = {
        "start": 0,
        "end": 5,
        "entity_type": "PERSON",
        "score": 0.9,
        "source": "fake",
        "priority": PRIORITY_ML,
    }
    fields.update(overrides)
    return Span(**fields)  # type: ignore[arg-type]


def test_priorities_follow_the_layer_order() -> None:
    assert (
        PRIORITY_LITERAL > PRIORITY_DICTIONARY > PRIORITY_RULE > PRIORITY_ML > PRIORITY_PROPAGATION
    )
    assert (PRIORITY_LITERAL, PRIORITY_DICTIONARY, PRIORITY_RULE, PRIORITY_ML) == (100, 80, 60, 40)
    assert PRIORITY_PROPAGATION == 20


@pytest.mark.parametrize("name", BUILTIN_TYPES)
def test_builtin_entity_type_constants_are_valid_and_named_after_their_value(name: str) -> None:
    assert getattr(types, name) == name
    assert validate_entity_type(name) == name


def test_valid_span_is_constructed() -> None:
    span = _span()
    assert (span.start, span.end, span.entity_type, span.score, span.source, span.priority) == (
        0,
        5,
        "PERSON",
        0.9,
        "fake",
        PRIORITY_ML,
    )


def test_empty_span_is_allowed_at_construction() -> None:
    span = _span(start=3, end=3)
    assert span.start == span.end == 3


@pytest.mark.parametrize("score", [0.0, 1.0])
def test_score_bounds_are_inclusive(score: float) -> None:
    assert _span(score=score).score == score


def test_span_is_immutable() -> None:
    span = _span()
    with pytest.raises(AttributeError):
        span.start = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"start": -1}, "start"),
        ({"start": 6, "end": 5}, "end"),
        ({"score": -0.01}, "score"),
        ({"score": 1.01}, "score"),
        ({"score": float("nan")}, "score"),
        ({"entity_type": "person"}, "entity type"),
        ({"entity_type": ""}, "entity type"),
    ],
    ids=[
        "negative-start",
        "end-before-start",
        "score-below-zero",
        "score-above-one",
        "score-nan",
        "lower-case-type",
        "empty-type",
    ],
)
def test_span_with_invalid_field_raises_value_error(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _span(**overrides)


def test_span_error_message_does_not_contain_the_entity_type_value() -> None:
    with pytest.raises(ValueError, match="entity type") as excinfo:
        _span(entity_type="Juan Pérez")
    assert "Juan" not in str(excinfo.value)


@pytest.mark.parametrize("name", ["PERSON", "CREDIT_CARD", "A", "A" * MAX_ENTITY_TYPE_LENGTH])
def test_validate_entity_type_accepts_upper_case_names(name: str) -> None:
    assert validate_entity_type(name) == name


@pytest.mark.parametrize(
    "name",
    ["person", "PERSON1", "_PERSON", "", "A" * (MAX_ENTITY_TYPE_LENGTH + 1), "PERSON\n", "ÉLAN"],
    ids=[
        "lower-case",
        "digit",
        "leading-underscore",
        "empty",
        "41-characters",
        "trailing-newline",
        "non-ascii",
    ],
)
def test_validate_entity_type_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ValueError, match="entity type") as excinfo:
        validate_entity_type(name)
    if name:
        assert name not in str(excinfo.value)


def test_max_entity_type_length_is_forty() -> None:
    assert MAX_ENTITY_TYPE_LENGTH == 40


class _StaticDetector:
    name = "static"

    def detect(self, text: str) -> list[Span]:
        return [_span(start=0, end=len(text))]


def test_a_class_with_name_and_detect_satisfies_the_detector_protocol() -> None:
    detector: Detector = _StaticDetector()
    assert detector.name == "static"
    assert detector.detect("Alice") == [_span(start=0, end=5)]

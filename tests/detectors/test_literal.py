from aliasgate.detectors.literal import LiteralDetector
from aliasgate.types import PRIORITY_LITERAL, Span


def _literal(start: int, end: int) -> Span:
    return Span(start, end, "LITERAL", 1.0, "literal", PRIORITY_LITERAL)


def test_every_placeholder_shaped_text_is_a_literal_span() -> None:
    text = "see [PERSON_1] and [org 2]"
    assert LiteralDetector().detect(text) == [_literal(4, 14), _literal(19, 26)]
    assert [text[4:14], text[19:26]] == ["[PERSON_1]", "[org 2]"]


def test_plain_text_yields_nothing() -> None:
    assert LiteralDetector().detect("Alice met Bob at ACME [sic] on [1].") == []


def test_escaped_brackets_are_part_of_the_literal() -> None:
    assert LiteralDetector().detect("\\[PERSON_1\\]") == [_literal(0, 12)]


def test_a_backslash_before_an_unescaped_placeholder_is_not_part_of_the_literal() -> None:
    assert LiteralDetector().detect("\\[PERSON_1]") == [_literal(1, 11)]


def test_name_is_literal() -> None:
    assert LiteralDetector.name == "literal"

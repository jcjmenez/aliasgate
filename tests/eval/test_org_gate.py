"""The CI gate on the organisation corpus (slice 19): with the full names detected, their
distinctive words and the names without their legal form are replaced and linked to them.

It runs with `orgs.yaml` (a dictionary of every organisation's full name) and the default
configuration otherwise, so `detection.person_tokens` and `detection.org_tokens` are on. If a
gate is not met, fix the implementation: change neither the corpus nor a threshold (AGENTS.md
rule 9). The category `common`, whose distinctive word is a common word that slice 19 leaves as
written, is measured and documented in docs/evaluation.md, not asserted.
"""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pytest

from aliasgate.config import load_settings
from aliasgate.engine import Pseudonymizer
from aliasgate.eval.corpus import Example, load_corpus
from aliasgate.eval.metrics import LinkScore, Report, evaluate
from aliasgate.factory import build_pseudonymizer
from aliasgate.types import Span

CORPUS = Path(__file__).parent / "corpus"
LANGUAGES = ("en", "es")
COVERED = ("full", "bare", "word", "before", "multi", "shared", "generic", "lower")


@dataclass(frozen=True, slots=True)
class _Category:
    examples: tuple[Example, ...]
    spans: tuple[list[Span], ...]  # what the engine replaces in each example
    report: Report


def _category(example: Example) -> str:
    return example.id.split("-", 1)[1].rsplit("-", 1)[0]


@pytest.fixture(scope="module")
def pseudonymizer() -> Pseudonymizer:
    settings = load_settings(CORPUS / "orgs.yaml", {})
    assert settings.detection.person_tokens
    assert settings.detection.org_tokens
    return build_pseudonymizer(settings)


@pytest.fixture(scope="module")
def categories(pseudonymizer: Pseudonymizer) -> dict[tuple[str, str], _Category]:
    grouped: dict[tuple[str, str], list[Example]] = defaultdict(list)
    for lang in LANGUAGES:
        for example in load_corpus(CORPUS / f"orgs.{lang}.jsonl"):
            grouped[lang, _category(example)].append(example)
    return {
        key: _Category(
            tuple(examples),
            tuple(pseudonymizer.analyze([example.text])[0] for example in examples),
            evaluate(pseudonymizer, examples),
        )
        for key, examples in grouped.items()
    }


def _orgs(category: _Category) -> LinkScore:
    (score,) = [score for score in category.report.linking if score.entity_type == "ORG"]
    return score


def _uncovered(found: _Category) -> list[tuple[str, int]]:
    """Letters and digits of gold ORG spans that no predicted span covers, by example id and
    offset."""
    return [
        (example.id, index)
        for example, spans in zip(found.examples, found.spans, strict=True)
        for gold in example.spans
        if gold.entity_type == "ORG"
        for index in range(gold.start, gold.end)
        if example.text[index].isalnum()
        and not any(span.start <= index < span.end for span in spans)
    ]


@pytest.mark.parametrize("category", COVERED)
@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_letter_and_digit_of_every_organisation_mention_is_covered(
    categories: dict[tuple[str, str], _Category], lang: str, category: str
) -> None:
    found = categories[lang, category]
    assert _uncovered(found) == []
    assert len(found.examples) == 20


@pytest.mark.parametrize("category", COVERED)
@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_pair_of_mentions_of_one_organisation_is_linked_and_no_pair_is_wrong(
    categories: dict[tuple[str, str], _Category], lang: str, category: str
) -> None:
    score = _orgs(categories[lang, category])
    assert score.wrong_pairs == 0
    assert score.same_pairs > 0
    assert score.link_recall == 1.0


@pytest.mark.parametrize("lang", LANGUAGES)
def test_a_generic_word_on_its_own_is_not_replaced(
    categories: dict[tuple[str, str], _Category], lang: str
) -> None:
    found = categories[lang, "generic"]
    outside = [
        (example.id, span.start)
        for example, spans in zip(found.examples, found.spans, strict=True)
        for span in spans
        if not any(gold.start <= span.start and span.end <= gold.end for gold in example.spans)
    ]
    assert outside == []


@pytest.mark.parametrize("lang", LANGUAGES)
def test_there_are_no_round_trip_failures(
    categories: dict[tuple[str, str], _Category], lang: str
) -> None:
    reports = [found.report for (language, _), found in categories.items() if language == lang]
    assert len(reports) == 9
    assert sum(report.round_trip_failures for report in reports) == 0
    assert sum(report.examples for report in reports) == 180

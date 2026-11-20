"""The CI gate: recall of the deterministic layers on the committed synthetic corpora.

If a threshold is not reached, fix the rule, not the threshold (slice 11).
"""

from pathlib import Path

import pytest

from aliasgate.config import DetectionSettings, MLSettings, Settings
from aliasgate.eval.corpus import load_corpus
from aliasgate.eval.metrics import Report, evaluate
from aliasgate.eval.report import format_table
from aliasgate.factory import build_pseudonymizer

CORPUS = Path(__file__).parent / "corpus"
LANGUAGES = ("en", "es")
RULE_TYPES = ("EMAIL", "IBAN", "CREDIT_CARD", "NATIONAL_ID", "TAX_ID", "IP_ADDRESS")


@pytest.fixture(scope="module")
def reports() -> dict[str, Report]:
    """One report per corpus with the default configuration: rules only, no dictionary, no ML."""
    pseudonymizer = build_pseudonymizer(Settings())
    return {
        lang: evaluate(pseudonymizer, load_corpus(CORPUS / f"synthetic.{lang}.jsonl"))
        for lang in LANGUAGES
    }


def _recall_overlap(report: Report, entity_type: str) -> float:
    (score,) = [score for score in report.by_type if score.entity_type == entity_type]
    assert score.gold > 0
    return score.recall_overlap


@pytest.mark.parametrize("lang", LANGUAGES)
@pytest.mark.parametrize("entity_type", RULE_TYPES)
def test_rule_types_reach_99_percent_overlap_recall(
    reports: dict[str, Report], lang: str, entity_type: str
) -> None:
    assert _recall_overlap(reports[lang], entity_type) >= 0.99


@pytest.mark.parametrize("lang", LANGUAGES)
def test_phones_reach_95_percent_overlap_recall(reports: dict[str, Report], lang: str) -> None:
    assert _recall_overlap(reports[lang], "PHONE") >= 0.95


@pytest.mark.parametrize("lang", LANGUAGES)
def test_at_most_2_percent_of_examples_without_entities_have_a_predicted_span(
    reports: dict[str, Report], lang: str
) -> None:
    report = reports[lang]
    assert report.entity_free > 0
    assert report.entity_free_flagged <= 0.02 * report.entity_free


@pytest.mark.parametrize("lang", LANGUAGES)
def test_there_are_no_round_trip_failures(reports: dict[str, Report], lang: str) -> None:
    assert reports[lang].round_trip_failures == 0


@pytest.mark.ml
def test_ml_baseline_for_names_organisations_and_addresses(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Prints the ML layer's numbers without asserting thresholds: the published baseline."""
    pytest.importorskip("gliner")
    settings = Settings(detection=DetectionSettings(ml=MLSettings(enabled=True)))
    pseudonymizer = build_pseudonymizer(settings)  # one model load, shared by both corpora
    tables = [
        format_table(
            lang,
            evaluate(
                pseudonymizer,
                load_corpus(CORPUS / f"synthetic.{lang}.jsonl"),
                types={"PERSON", "ORG", "ADDRESS"},
            ),
        )
        for lang in LANGUAGES
    ]
    with capsys.disabled():
        print(f"\nmodel {settings.detection.ml.model} revision {settings.detection.ml.revision}")
        print("\n\n".join(tables))

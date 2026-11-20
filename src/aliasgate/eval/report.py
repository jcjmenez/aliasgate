"""Renders evaluation reports as a table or JSON: numbers, entity types and example ids only."""

import json
from collections.abc import Mapping
from typing import Final

from aliasgate.eval.metrics import Miss, Report

_COLUMNS: Final = (
    ("type", 14),
    ("gold", 6),
    ("predicted", 10),
    ("exact", 6),
    ("overlap", 8),
    ("recall_exact", 13),
    ("recall_overlap", 15),
    ("precision_exact", 16),
)


def format_table(lang: str, report: Report, *, misses: int = 0) -> str:
    """A plain-text summary of one language, listing up to `misses` missed spans."""
    lines = [
        f"{lang}: {report.examples} examples, leak rate {report.leak_rate:.4f} "
        f"({report.leaked_chars} of {report.gold_chars} characters), "
        f"round-trip failures {report.round_trip_failures}",
        f"examples without entities: {report.entity_free}, with a predicted span: "
        f"{report.entity_free_flagged} ({report.entity_free_flag_rate:.1%})",
        "".join(
            name.ljust(width) if index == 0 else name.rjust(width)
            for index, (name, width) in enumerate(_COLUMNS)
        ),
    ]
    for score in report.by_type:
        values = (
            score.entity_type,
            str(score.gold),
            str(score.predicted),
            str(score.exact_tp),
            str(score.overlap_tp),
            f"{score.recall_exact:.3f}",
            f"{score.recall_overlap:.3f}",
            f"{score.precision_exact:.3f}",
        )
        lines.append(
            "".join(
                value.ljust(width) if index == 0 else value.rjust(width)
                for index, (value, (_, width)) in enumerate(zip(values, _COLUMNS, strict=True))
            )
        )
    if misses > 0 and report.misses:
        shown = report.misses[:misses]
        lines.append(f"missed spans ({len(shown)} of {len(report.misses)}):")
        lines.extend(
            f"  {miss.example_id} {miss.start}..{miss.end} {miss.entity_type}, "
            f"{miss.leaked_chars} characters not covered"
            for miss in shown
        )
    return "\n".join(lines)


def format_json(reports: Mapping[str, Report], *, misses: int = 0) -> str:
    """All languages as one JSON object keyed by language, with up to `misses` missed spans."""
    return json.dumps(
        {lang: _as_dict(report, misses) for lang, report in reports.items()}, indent=2
    )


def _as_dict(report: Report, misses: int) -> dict[str, object]:
    return {
        "examples": report.examples,
        "leak_rate": report.leak_rate,
        "leaked_chars": report.leaked_chars,
        "gold_chars": report.gold_chars,
        "round_trip_failures": report.round_trip_failures,
        "examples_without_entities": report.entity_free,
        "flagged_without_entities": report.entity_free_flagged,
        "by_type": [
            {
                "entity_type": score.entity_type,
                "gold": score.gold,
                "predicted": score.predicted,
                "exact_tp": score.exact_tp,
                "overlap_tp": score.overlap_tp,
                "recall_exact": score.recall_exact,
                "recall_overlap": score.recall_overlap,
                "precision_exact": score.precision_exact,
            }
            for score in report.by_type
        ],
        "missed_spans": len(report.misses),
        "misses": [_miss(miss) for miss in report.misses[:misses]],
    }


def _miss(miss: Miss) -> dict[str, object]:
    return {
        "example_id": miss.example_id,
        "start": miss.start,
        "end": miss.end,
        "entity_type": miss.entity_type,
        "leaked_chars": miss.leaked_chars,
    }

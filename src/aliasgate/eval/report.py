"""Renders evaluation reports as a table or JSON: numbers, entity types, example ids and
offsets only. Entity ids of the corpus are never rendered, since they may be made from values."""

import json
from collections.abc import Mapping, Sequence
from typing import Final

from aliasgate.eval.metrics import LinkScore, Miss, Report, WrongMerge

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
_LINK_COLUMNS: Final = (
    ("type", 14),
    ("mentions", 9),
    ("placed", 7),
    ("same_pairs", 11),
    ("linked", 7),
    ("wrong", 6),
    ("link_recall", 12),
    ("link_precision", 15),
)


def _row(values: Sequence[str], columns: Sequence[tuple[str, int]]) -> str:
    return "".join(
        value.ljust(width) if index == 0 else value.rjust(width)
        for index, (value, (_, width)) in enumerate(zip(values, columns, strict=True))
    )


def format_table(lang: str, report: Report, *, misses: int = 0, wrong_merges: int = 0) -> str:
    """A plain-text summary of one language, with a linking table when the corpus has entity
    ids, listing up to `misses` missed spans and up to `wrong_merges` wrong merges."""
    lines = [
        f"{lang}: {report.examples} examples, leak rate {report.leak_rate:.4f} "
        f"({report.leaked_chars} of {report.gold_chars} characters), "
        f"round-trip failures {report.round_trip_failures}",
        f"examples without entities: {report.entity_free}, with a predicted span: "
        f"{report.entity_free_flagged} ({report.entity_free_flag_rate:.1%})",
        _row([name for name, _ in _COLUMNS], _COLUMNS),
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
        lines.append(_row(values, _COLUMNS))
    if report.linking:
        lines.append("linking (pairs of mentions within an example):")
        lines.append(_row([name for name, _ in _LINK_COLUMNS], _LINK_COLUMNS))
        lines.extend(_row(_link_values(link), _LINK_COLUMNS) for link in report.linking)
    if misses > 0 and report.misses:
        shown = report.misses[:misses]
        lines.append(f"missed spans ({len(shown)} of {len(report.misses)}):")
        lines.extend(
            f"  {miss.example_id} {miss.start}..{miss.end} {miss.entity_type}, "
            f"{miss.leaked_chars} characters not covered"
            for miss in shown
        )
    if wrong_merges > 0 and report.wrong_merges:
        merges = report.wrong_merges[:wrong_merges]
        lines.append(f"wrong merges ({len(merges)} of {len(report.wrong_merges)}):")
        lines.extend(
            f"  {merge.example_id} {merge.first[0]}..{merge.first[1]} and "
            f"{merge.second[0]}..{merge.second[1]} {merge.entity_type}"
            for merge in merges
        )
    return "\n".join(lines)


def _link_values(link: LinkScore) -> tuple[str, ...]:
    return (
        link.entity_type,
        str(link.mentions),
        str(link.placed),
        str(link.same_pairs),
        str(link.linked_pairs),
        str(link.wrong_pairs),
        f"{link.link_recall:.3f}",
        f"{link.link_precision:.3f}",
    )


def format_json(reports: Mapping[str, Report], *, misses: int = 0, wrong_merges: int = 0) -> str:
    """All languages as one JSON object keyed by language, with up to `misses` missed spans and
    up to `wrong_merges` wrong merges."""
    return json.dumps(
        {lang: _as_dict(report, misses, wrong_merges) for lang, report in reports.items()},
        indent=2,
    )


def _as_dict(report: Report, misses: int, wrong_merges: int) -> dict[str, object]:
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
        "linking": [
            {
                "entity_type": link.entity_type,
                "mentions": link.mentions,
                "placed": link.placed,
                "same_pairs": link.same_pairs,
                "linked_pairs": link.linked_pairs,
                "wrong_pairs": link.wrong_pairs,
                "link_recall": link.link_recall,
                "link_precision": link.link_precision,
            }
            for link in report.linking
        ],
        "wrong_merge_pairs": len(report.wrong_merges),
        "wrong_merges": [_merge(merge) for merge in report.wrong_merges[:wrong_merges]],
    }


def _miss(miss: Miss) -> dict[str, object]:
    return {
        "example_id": miss.example_id,
        "start": miss.start,
        "end": miss.end,
        "entity_type": miss.entity_type,
        "leaked_chars": miss.leaked_chars,
    }


def _merge(merge: WrongMerge) -> dict[str, object]:
    return {
        "example_id": merge.example_id,
        "first": list(merge.first),
        "second": list(merge.second),
        "entity_type": merge.entity_type,
    }

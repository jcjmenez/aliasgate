"""`python -m aliasgate.eval CORPUS...`: measure detection on labelled corpora, per language.

The engine is built from the configuration exactly as the gateway builds it. The output holds
numbers, entity types, example ids and offsets, never example text or entity ids. Exit codes:
0, 1 when a leak rate is above `--max-leak-rate`, 2 for invalid input or a failure.
"""

import argparse
import math
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml

from aliasgate.config import load_settings
from aliasgate.engine import DetectionError
from aliasgate.eval.corpus import Example, load_corpus
from aliasgate.eval.metrics import Report, evaluate
from aliasgate.eval.report import format_json, format_table
from aliasgate.factory import build_pseudonymizer
from aliasgate.types import validate_entity_type


def _rate(value: str) -> float:
    rate = float(value)
    if math.isnan(rate) or not 0.0 <= rate <= 1.0:
        raise argparse.ArgumentTypeError("must be a number between 0 and 1")
    return rate


def _types(value: str) -> frozenset[str]:
    names = [name.strip() for name in value.split(",")]
    try:
        return frozenset(validate_entity_type(name) for name in names)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "must be comma-separated entity types such as EMAIL,IBAN"
        ) from None


def _count(value: str) -> int:
    count = int(value)
    if count < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return count


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m aliasgate.eval",
        description="Measure detection on labelled JSON Lines corpora, per language.",
    )
    parser.add_argument("corpus", nargs="+", type=Path, help="JSON Lines corpus files")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="gateway configuration; ALIASGATE_* environment variables override it",
    )
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument(
        "--max-leak-rate",
        type=_rate,
        default=None,
        metavar="RATE",
        help="exit with code 1 if a language's leak rate is above RATE",
    )
    parser.add_argument(
        "--types", type=_types, default=None, metavar="TYPES", help="e.g. EMAIL,IBAN"
    )
    parser.add_argument(
        "--show-misses",
        type=_count,
        default=0,
        metavar="N",
        help="list up to N missed spans by example id and offsets",
    )
    parser.add_argument(
        "--show-wrong-merges",
        type=_count,
        default=0,
        metavar="N",
        help="list up to N pairs of mentions of two entities given one placeholder family",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] = os.environ) -> int:
    """Evaluate the corpora, print the report and return the exit code."""
    args = _parser().parse_args(argv)
    try:
        settings = load_settings(args.config, env)
        by_lang = _by_language([example for path in args.corpus for example in load_corpus(path)])
        pseudonymizer = build_pseudonymizer(settings)
    except yaml.YAMLError:
        # The parser's message quotes the offending line.
        return _fail("the configuration file is not valid YAML")
    except (ValueError, RuntimeError, OSError) as exc:
        # Configuration, corpus and dictionary errors never repeat their input.
        return _fail(f"invalid configuration or corpus: {exc}")
    try:
        reports = {
            lang: evaluate(pseudonymizer, examples, types=args.types)
            for lang, examples in sorted(by_lang.items())
        }
    except DetectionError as exc:
        return _fail(str(exc))
    except Exception as exc:  # anything else may carry text in its message
        return _fail(f"evaluation failed ({type(exc).__name__})")
    if args.format == "json":
        print(format_json(reports, misses=args.show_misses, wrong_merges=args.show_wrong_merges))
    else:
        print(
            "\n\n".join(
                format_table(
                    lang, report, misses=args.show_misses, wrong_merges=args.show_wrong_merges
                )
                for lang, report in reports.items()
            )
        )
    return 1 if _above(reports, args.max_leak_rate) else 0


def _by_language(examples: list[Example]) -> dict[str, list[Example]]:
    by_lang: dict[str, list[Example]] = {}
    seen: set[str] = set()
    for example in examples:
        if example.id in seen:
            raise ValueError(f"example id {example.id!r} appears in more than one place")
        seen.add(example.id)
        by_lang.setdefault(example.lang, []).append(example)
    return by_lang


def _above(reports: Mapping[str, Report], limit: float | None) -> bool:
    return limit is not None and any(report.leak_rate > limit for report in reports.values())


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

"""Rule-based detection of structured identifiers, validated by checksums or libraries."""

import ipaddress
import re
import string
import sys
from collections.abc import Callable, Collection, Iterator, Sequence
from dataclasses import dataclass
from typing import Final

import phonenumbers

from aliasgate.detectors.checksums import (
    es_cif_valid,
    es_dni_valid,
    es_nie_valid,
    iban_valid,
    luhn_valid,
    us_ssn_plausible,
)
from aliasgate.types import (
    CREDIT_CARD,
    EMAIL,
    IBAN,
    IP_ADDRESS,
    NATIONAL_ID,
    PHONE,
    PRIORITY_RULE,
    SECRET,
    TAX_ID,
    URL,
    Span,
)

DEFAULT_RULE_TYPES: Final[frozenset[str]] = frozenset(
    {EMAIL, PHONE, IBAN, CREDIT_CARD, NATIONAL_ID, TAX_ID, IP_ADDRESS, SECRET}
)  # "URL" exists but is off by default

_RULE_TYPES: Final = DEFAULT_RULE_TYPES | {URL}
_SOURCE: Final = "rules"
_PHONE_SCORE: Final = 0.9
_SECRET_SCORE: Final = 1.0


def _ipv4_valid(candidate: str) -> bool:
    try:
        ipaddress.IPv4Address(candidate)
    except ValueError:
        return False
    return True


def _ipv6_valid(candidate: str) -> bool:
    # "::" alone parses, but it identifies nobody and is common in code ("x :: Int").
    if not any(char in string.hexdigits for char in candidate):
        return False
    try:
        ipaddress.IPv6Address(candidate)
    except ValueError:
        return False
    return True


def _drop_last_group(separators: str, min_length: int) -> Callable[[str], str | None]:
    """Shortener that drops the last separator-delimited group, while at least `min_length`
    non-separator characters would remain."""

    def shorten(candidate: str) -> str | None:
        cut = max(candidate.rfind(separator) for separator in separators)
        if cut <= 0:
            return None
        shorter = candidate[:cut]
        remaining = len(shorter) - sum(shorter.count(separator) for separator in separators)
        return shorter if remaining >= min_length else None

    return shorten


@dataclass(frozen=True, slots=True)
class _Rule:
    """A candidate pattern, an optional validator, and how to shorten a rejected candidate."""

    entity_type: str
    pattern: re.Pattern[str]
    score: float
    validate: Callable[[str], bool] | None = None
    shorten: Callable[[str], str | None] | None = None


_RULES: Final[tuple[_Rule, ...]] = (
    _Rule(
        EMAIL,
        re.compile(r"(?<![\w.%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![\w-])"),
        1.0,
    ),
    _Rule(
        IBAN,
        re.compile(
            r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?"
            r"(?![A-Za-z0-9])"
        ),
        1.0,
        iban_valid,
        _drop_last_group(" ", min_length=15),
    ),
    _Rule(
        CREDIT_CARD,
        re.compile(r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])"),
        1.0,
        luhn_valid,
        _drop_last_group(" -", min_length=13),
    ),
    _Rule(
        NATIONAL_ID,
        re.compile(r"(?<![A-Za-z0-9])\d{8}[- ]?[A-Za-z](?![A-Za-z0-9])"),
        1.0,
        es_dni_valid,
    ),
    _Rule(
        NATIONAL_ID,
        re.compile(r"(?<![A-Za-z0-9])[XYZxyz][- ]?\d{7}[- ]?[A-Za-z](?![A-Za-z0-9])"),
        1.0,
        es_nie_valid,
    ),
    _Rule(
        NATIONAL_ID,
        re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])"),
        0.85,
        us_ssn_plausible,
    ),
    _Rule(
        TAX_ID,
        re.compile(r"(?<![A-Za-z0-9])[A-Za-z]\d{7}[0-9A-Ja-j](?![A-Za-z0-9])"),
        1.0,
        es_cif_valid,
    ),
    _Rule(
        IP_ADDRESS,
        # A sentence may end right after the address, so a dot is a boundary unless a digit
        # follows it.
        re.compile(r"(?<!\d)(?<!\d\.)(?:\d{1,3}\.){3}\d{1,3}(?!\d)(?!\.\d)"),
        0.9,
        _ipv4_valid,
    ),
    _Rule(
        IP_ADDRESS,
        re.compile(
            r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,8}"
            r"(?:(?:\d{1,3}\.){3}\d{1,3}|[0-9A-Fa-f]{1,4})?(?![\w:])(?!\.\d)"
        ),
        0.9,
        _ipv6_valid,
    ),
    _Rule(
        URL,
        # The last character excludes .,;:!? so that sentence punctuation is not part of it.
        re.compile(r"https?://[^\s<>\"')\]]*[^\s<>\"')\].,;:!?]"),
        0.9,
    ),
    _Rule(SECRET, re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"), _SECRET_SCORE),
    _Rule(SECRET, re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{36,}"), _SECRET_SCORE),
    _Rule(SECRET, re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{50,}"), _SECRET_SCORE),
    _Rule(SECRET, re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])"), _SECRET_SCORE),
    _Rule(SECRET, re.compile(r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{10,}"), _SECRET_SCORE),
    _Rule(
        SECRET,
        re.compile(
            r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"
        ),
        _SECRET_SCORE,
    ),
)

_PEM_BEGIN: Final = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_PEM_END: Final = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")


def _scan(rule: _Rule, text: str) -> Iterator[tuple[int, int]]:
    """Yield (start, end) of every accepted candidate, leftmost first.

    A rejected candidate is shortened while that is possible; if no version validates, the
    search resumes one character after its start, so a valid value that begins inside a
    rejected candidate is still found.
    """
    position = 0
    while (match := rule.pattern.search(text, position)) is not None:
        start = match.start()
        length = _accepted_length(rule, match.group())
        if length is None:
            position = start + 1
        else:
            yield start, start + length
            position = start + length


def _accepted_length(rule: _Rule, candidate: str) -> int | None:
    if rule.validate is None:
        return len(candidate)
    current: str | None = candidate
    while current is not None:
        if rule.validate(current):
            return len(current)
        current = rule.shorten(current) if rule.shorten is not None else None
    return None


def _scan_pem(text: str) -> Iterator[tuple[int, int]]:
    """Yield each private key block from its BEGIN line to the first END line after it.

    Same result as a non-greedy DOTALL pattern, in linear time: once a BEGIN line has no END
    line after it, no later BEGIN line has one either.
    """
    position = 0
    while (begin := _PEM_BEGIN.search(text, position)) is not None:
        end = _PEM_END.search(text, begin.end())
        if end is None:
            return
        yield begin.start(), end.end()
        position = end.end()


class RuleDetector:
    """Finds structured identifiers with patterns plus checksum or library validation.

    Deterministic: the same text always yields the same spans, sorted by (start, end). Overlaps
    between rules are kept; resolving them is the resolver's job.
    """

    name = "rules"

    def __init__(
        self,
        *,
        enabled_types: Collection[str] = DEFAULT_RULE_TYPES,
        phone_regions: Sequence[str] = ("ES", "US"),
    ) -> None:
        enabled = frozenset(enabled_types)
        unknown = enabled - _RULE_TYPES
        if unknown:
            raise ValueError(
                f"{len(unknown)} unknown rule type(s) in enabled_types; "
                f"known types are {', '.join(sorted(_RULE_TYPES))}"
            )
        regions = tuple(dict.fromkeys(phone_regions))
        if any(region not in phonenumbers.SUPPORTED_REGIONS for region in regions):
            raise ValueError("phone_regions must contain only supported upper-case region codes")
        if PHONE in enabled and not regions:
            raise ValueError("phone_regions must not be empty when PHONE is enabled")
        self._rules = tuple(rule for rule in _RULES if rule.entity_type in enabled)
        self._secrets = SECRET in enabled
        self._phone_regions = regions if PHONE in enabled else ()

    def detect(self, text: str) -> list[Span]:
        """Spans of every enabled rule, sorted by (start, end)."""
        spans = [
            Span(start, end, rule.entity_type, rule.score, _SOURCE, PRIORITY_RULE)
            for rule in self._rules
            for start, end in _scan(rule, text)
        ]
        if self._secrets:
            spans.extend(
                Span(start, end, SECRET, _SECRET_SCORE, _SOURCE, PRIORITY_RULE)
                for start, end in _scan_pem(text)
            )
        spans.extend(
            Span(start, end, PHONE, _PHONE_SCORE, _SOURCE, PRIORITY_RULE)
            for start, end in self._phone_offsets(text)
        )
        spans.sort(key=lambda span: (span.start, span.end))
        return spans

    def _phone_offsets(self, text: str) -> list[tuple[int, int]]:
        offsets: dict[tuple[int, int], None] = {}
        for region in self._phone_regions:
            # The library gives up silently after max_tries invalid candidates (65535 by
            # default), which would leave later numbers in a long text undetected.
            matcher = phonenumbers.PhoneNumberMatcher(
                text, region, leniency=phonenumbers.Leniency.VALID, max_tries=sys.maxsize
            )
            for match in matcher:
                offsets[(match.start, match.end)] = None
        return list(offsets)

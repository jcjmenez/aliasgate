"""Rule-based detection of structured identifiers, validated by checksums or libraries."""

import ipaddress
import re
import string
import sys
from bisect import bisect_right
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
_ISBN_LENGTH: Final = 13
_ISBN_PREFIXES: Final = ("978", "979")
_INTERNATIONAL_SOURCES: Final = frozenset(
    {
        phonenumbers.CountryCodeSource.FROM_NUMBER_WITH_PLUS_SIGN,
        phonenumbers.CountryCodeSource.FROM_NUMBER_WITH_IDD,
    }
)
_SECRET_SCORE: Final = 1.0
# Above the phone score: nine digits grouped 3-2-4 with hyphens are written as an SSN, even
# when they also form a valid Spanish number.
_SSN_SCORE: Final = 0.95
# Python's \w does not match combining marks, so a decomposed accent would cut an address.
_MARKS: Final = "\u0300-\u036f"


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


def _card_valid(candidate: str) -> bool:
    """Luhn-valid, and not 13 digits starting with 978 or 979: that is an ISBN-13 (the EAN
    "Bookland" prefix), and no card network issues 13-digit numbers with that prefix."""
    digits = candidate.replace(" ", "").replace("-", "")
    if len(digits) == _ISBN_LENGTH and digits.startswith(_ISBN_PREFIXES):
        return False
    return luhn_valid(candidate)


def _without_dots(validate: Callable[[str], bool]) -> Callable[[str], bool]:
    """Validator that ignores the dots grouping the digits, as in 12.345.678-Z."""

    def check(candidate: str) -> bool:
        return validate(candidate.replace(".", ""))

    return check


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
    """A candidate pattern, an optional validator, how to shorten a rejected candidate, and the
    pattern group that is the candidate (0, the whole match, unless the match has context)."""

    entity_type: str
    pattern: re.Pattern[str]
    score: float
    validate: Callable[[str], bool] | None = None
    shorten: Callable[[str], str | None] | None = None
    group: int = 0


_RULES: Final[tuple[_Rule, ...]] = (
    _Rule(
        EMAIL,
        re.compile(
            # Letters of any script. An apostrophe only between two other characters, so that
            # quotes around an address stay outside it; (?<!\w') keeps the scan from starting
            # again after every apostrophe, which would make it quadratic.
            rf"(?<![\w.%+-])(?<![{_MARKS}])(?<!\w')"
            rf"(?:[\w.%+-]|[{_MARKS}])+(?:'(?:[\w.%+-]|[{_MARKS}])+)*"
            rf"@(?:[^\W_]|[{_MARKS}]|-)+(?:\.(?:[^\W_]|[{_MARKS}]|-)+)+"
            rf"(?![\w-])(?![{_MARKS}])"
        ),
        1.0,
    ),
    _Rule(
        IBAN,
        re.compile(
            # Any letter case: people write IBANs in lower case too, and the checksum still
            # filters out words that happen to have the shape.
            r"(?<![A-Za-z0-9])[A-Za-z]{2}\d{2}(?:[ ]?[A-Za-z0-9]{4}){2,7}"
            r"(?:[ ]?[A-Za-z0-9]{1,3})?(?![A-Za-z0-9])"
        ),
        1.0,
        iban_valid,
        _drop_last_group(" ", min_length=15),
    ),
    _Rule(
        CREDIT_CARD,
        re.compile(r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])"),
        1.0,
        _card_valid,
        _drop_last_group(" -", min_length=13),
    ),
    # Spanish identifiers: the digits may be grouped with dots as Spanish numbers are written
    # (12.345.678-Z); the lookarounds keep a dotted form out of a longer dotted number.
    _Rule(
        NATIONAL_ID,
        re.compile(
            r"(?<![A-Za-z0-9])(?<!\d\.)(?:\d{8}|\d{2}\.\d{3}\.\d{3})[- ]?[A-Za-z](?![A-Za-z0-9])"
        ),
        1.0,
        _without_dots(es_dni_valid),
    ),
    _Rule(
        NATIONAL_ID,
        re.compile(
            r"(?<![A-Za-z0-9])[XYZxyz][- ]?(?:\d{7}|\d\.\d{3}\.\d{3})[- ]?[A-Za-z](?![A-Za-z0-9])"
        ),
        1.0,
        _without_dots(es_nie_valid),
    ),
    _Rule(
        NATIONAL_ID,
        re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])"),
        _SSN_SCORE,
        us_ssn_plausible,
    ),
    _Rule(
        NATIONAL_ID,
        # Nine digits without hyphens are an SSN only right after a keyword on the same line,
        # with no digit in between. The span is the number (group 1), not the keyword.
        re.compile(
            r"(?i)(?<![a-z0-9])(?:ssns?|ss\#|social[ \t]+security)(?![a-z])"
            r"[^\d\r\n]{0,25}?(?<!\d)(\d{3}[ ]?\d{2}[ ]?\d{4})(?![\d-])"
        ),
        _SSN_SCORE,
        us_ssn_plausible,
        group=1,
    ),
    _Rule(
        TAX_ID,
        re.compile(
            r"(?<![A-Za-z0-9])[A-Za-z][- ]?(?:\d{7}[0-9A-Ja-j]|\d{2}\.\d{3}\.\d{3})"
            r"(?![A-Za-z0-9])(?!\.\d)"
        ),
        1.0,
        _without_dots(es_cif_valid),
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
# A line break, or its JSON escape: a key can sit inside a JSON document that is one string.
_PEM_BREAK: Final = re.compile(r"\r?\n|\\r\\n|\\n")
_PEM_HEADER: Final = re.compile(r"(?:Proc-Type|DEK-Info):[^\r\n\\]*")
# Group 1 is the base64 of one line (with \/, the JSON escape of /); the line may end in spaces
# or tabs and must end at a break, at the end of the text or before a closing quote.
_PEM_BODY_LINE: Final = re.compile(r"((?:[A-Za-z0-9+/=]|\\/)+)[ \t]*(?=\r?\n|\\r\\n|\\n|\Z|[\"'])")


def _scan(rule: _Rule, text: str) -> Iterator[tuple[int, int]]:
    """Yield (start, end) of every accepted candidate, leftmost first.

    A rejected candidate is shortened while that is possible; if no version validates, the
    search resumes one character after the start of the match, so a valid value that begins
    inside a rejected candidate is still found.
    """
    position = 0
    while (match := rule.pattern.search(text, position)) is not None:
        start = match.start(rule.group)
        length = _accepted_length(rule, match.group(rule.group))
        if length is None:
            position = match.start() + 1
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
    """Yield each private key block from its BEGIN line to the first END line after it, or,
    when no END line follows, to the end of its base64 lines.

    Linear: once a BEGIN line has no END line after it, no later BEGIN line has one either, so
    the END line is searched for only until it is missing once.
    """
    position = 0
    end_lines_left = True
    while (begin := _PEM_BEGIN.search(text, position)) is not None:
        end = _PEM_END.search(text, begin.end()) if end_lines_left else None
        if end is not None:
            stop = end.end()
        else:
            end_lines_left = False
            stop = _pem_body_end(text, begin.end())
        yield begin.start(), stop
        position = stop


def _pem_body_end(text: str, position: int) -> int:
    """End of a key without END line: the header lines of an encrypted key and the blank line
    after them, then each line made only of base64 characters."""
    end = position
    has_headers = False
    while (newline := _PEM_BREAK.match(text, end)) and (
        header := _PEM_HEADER.match(text, newline.end())
    ):
        end = header.end()
        has_headers = True
    cursor = end
    if (
        has_headers
        and (newline := _PEM_BREAK.match(text, cursor))
        and _PEM_BREAK.match(text, newline.end())
    ):
        cursor = newline.end()
    while (newline := _PEM_BREAK.match(text, cursor)) and (
        line := _PEM_BODY_LINE.match(text, newline.end())
    ):
        end = line.end(1)
        cursor = line.end()
    return end


class RuleDetector:
    """Finds structured identifiers with patterns plus checksum or library validation.

    Deterministic: the same text always yields the same spans, sorted by (start, end). Overlaps
    between rules are kept, and resolving them is the resolver's job, with one exception: when
    URL is enabled, a span that lies entirely inside a URL span is dropped, so the URL is
    replaced whole.
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
        self._urls = URL in enabled
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
        return _without_spans_inside_urls(spans) if self._urls else spans

    def _phone_offsets(self, text: str) -> list[tuple[int, int]]:
        """Numbers written with an international prefix (+ or the region's IDD) need only be
        valid; national numbers must also be grouped as their country writes them, so that
        digit groups such as "2013-015180" in an order code are not taken for a phone."""
        offsets: dict[tuple[int, int], None] = {}
        for region in self._phone_regions:
            for match in _phone_matches(text, region, phonenumbers.Leniency.STRICT_GROUPING):
                offsets[(match.start, match.end)] = None
            for match in _phone_matches(text, region, phonenumbers.Leniency.VALID):
                if _has_international_prefix(match.raw_string, region):
                    offsets[(match.start, match.end)] = None
        return sorted(offsets)


def _without_spans_inside_urls(spans: list[Span]) -> list[Span]:
    """`spans` (sorted by start) without the non-URL spans that lie entirely inside a URL span.

    URL spans never overlap each other, so only the last URL starting at or before a span can
    contain it.
    """
    urls = [span for span in spans if span.entity_type == URL]
    starts = [url.start for url in urls]

    def inside_a_url(span: Span) -> bool:
        index = bisect_right(starts, span.start) - 1
        return index >= 0 and span.end <= urls[index].end

    return [span for span in spans if span.entity_type == URL or not inside_a_url(span)]


def _phone_matches(
    text: str, region: str, leniency: int
) -> Iterator[phonenumbers.PhoneNumberMatch]:
    # The library gives up silently after max_tries invalid candidates (65535 by default),
    # which would leave later numbers in a long text undetected.
    return iter(
        phonenumbers.PhoneNumberMatcher(text, region, leniency=leniency, max_tries=sys.maxsize)
    )


def _has_international_prefix(raw: str, region: str) -> bool:
    # The matcher clears the country code source of the numbers it returns, so parse again.
    number = phonenumbers.parse(raw, region, keep_raw_input=True)
    return number.country_code_source in _INTERNATIONAL_SOURCES

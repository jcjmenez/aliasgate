# Slice 02: rule-based detectors

## Goal

A `RuleDetector` finds structured identifiers (emails, phones, IBANs, cards, national and tax
identifiers, IP addresses, secrets, optionally URLs) with regular expressions and validates them
with checksums or libraries. This layer is fully deterministic.

## Depends on

Slice 01.

## Dependencies to add

```bash
uv add phonenumbers
```

## Files

| File | Action |
|---|---|
| `src/aliasgate/detectors/__init__.py` | create (empty docstring only) |
| `src/aliasgate/detectors/checksums.py` | create |
| `src/aliasgate/detectors/rules.py` | create |
| `tests/detectors/test_checksums.py`, `tests/detectors/test_rules.py` | create |

## Specification

### `checksums.py`

Pure functions on strings. Each one strips spaces and hyphens itself, is case-insensitive where
letters occur, returns `False` for malformed input and never raises.

```python
def luhn_valid(number: str) -> bool: ...          # 13–19 digits, Luhn mod 10
def iban_valid(iban: str) -> bool: ...            # 15–34 chars, ISO 13616 mod-97 == 1
def es_dni_valid(value: str) -> bool: ...         # 8 digits + letter
def es_nie_valid(value: str) -> bool: ...         # X/Y/Z + 7 digits + letter
def es_cif_valid(value: str) -> bool: ...         # letter + 7 digits + control digit or letter
def us_ssn_plausible(value: str) -> bool: ...     # AAA-GG-SSSS with exclusions
```

Algorithms:

- **DNI**: letter must equal `"TRWAGMYFPDXBNJZSQVHLCKE"[int(digits) % 23]`.
- **NIE**: replace the first letter X→0, Y→1, Z→2, then apply the DNI rule to the 8 digits.
- **CIF**: first character in `ABCDEFGHJNPQRSUVW`. Take the 7 digits `d1..d7`. Sum the digits in
  even positions (`d2`, `d4`, `d6`). For each digit in odd positions (`d1`, `d3`, `d5`, `d7`)
  double it and add the digits of the result. `control = (10 - total % 10) % 10`. The last
  character is valid if it is the digit `control` or the letter `"JABCDEFGHI"[control]`.
- **SSN**: area not `000`, not `666`, not `900–999`; group not `00`; serial not `0000`. There is
  no checksum, hence "plausible".

### `rules.py`

```python
DEFAULT_RULE_TYPES: Final[frozenset[str]] = frozenset({
    "EMAIL", "PHONE", "IBAN", "CREDIT_CARD", "NATIONAL_ID", "TAX_ID", "IP_ADDRESS", "SECRET",
})  # "URL" exists but is off by default

class RuleDetector:
    name = "rules"
    def __init__(self, *, enabled_types: Collection[str] = DEFAULT_RULE_TYPES,
                 phone_regions: Sequence[str] = ("ES", "US")) -> None: ...
    def detect(self, text: str) -> list[Span]: ...
```

Every span has `source="rules"`, `priority=PRIORITY_RULE`. `detect` returns spans from all
enabled rules without resolving overlaps between them (the resolver does that), sorted by
`(start, end)` for determinism. Unknown names in `enabled_types` raise `ValueError` in the
constructor.

Internally, model a rule as a small frozen dataclass (`entity_type`, compiled `pattern`,
optional `validate: Callable[[str], bool]`, `score`) so adding one is a three-line change. Phone
detection is the exception because it uses a library matcher.

| Type | Candidate pattern (starting point) | Validation | Score |
|---|---|---|---|
| `EMAIL` | `(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![\w-])` | none; a trailing `.` must not be part of the span | 1.0 |
| `IBAN` | `(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?(?![A-Za-z0-9])` | `iban_valid` | 1.0 |
| `CREDIT_CARD` | `(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])` | `luhn_valid` | 1.0 |
| `NATIONAL_ID` (DNI) | `(?<![A-Za-z0-9])\d{8}[- ]?[A-Za-z](?![A-Za-z0-9])` | `es_dni_valid` | 1.0 |
| `NATIONAL_ID` (NIE) | `(?<![A-Za-z0-9])[XYZxyz][- ]?\d{7}[- ]?[A-Za-z](?![A-Za-z0-9])` | `es_nie_valid` | 1.0 |
| `NATIONAL_ID` (SSN) | `(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])` | `us_ssn_plausible` | 0.85 |
| `TAX_ID` (CIF) | `(?<![A-Za-z0-9])[A-Za-z]\d{7}[0-9A-Ja-j](?![A-Za-z0-9])` | `es_cif_valid` | 1.0 |
| `IP_ADDRESS` (v4) | `(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])` | `ipaddress.IPv4Address` parses | 0.9 |
| `IP_ADDRESS` (v6) | a permissive candidate of hex groups and colons, at least two colons | `ipaddress.IPv6Address` parses | 0.9 |
| `URL` | `https?://[^\s<>"')\]]+` | strip trailing `.,;:!?` from the span | 0.9 |
| `SECRET` | see below | none | 1.0 |
| `PHONE` | `phonenumbers.PhoneNumberMatcher` | see below | 0.9 |

**IBAN and trailing words.** The greedy pattern can swallow a following upper-case word. If the
full candidate fails `iban_valid`, retry with the candidate shortened group by group from the
right (drop the last space-separated group) until it validates or fewer than 15 characters
remain.

**Phone.** For each region in `phone_regions`, run
`phonenumbers.PhoneNumberMatcher(text, region, leniency=phonenumbers.Leniency.VALID)` and emit a
span per match (`match.start`, `match.end`). Deduplicate identical `(start, end)` pairs. Numbers
written in international format (`+34…`) are found regardless of region. Verify the matcher API
against the installed version of `phonenumbers` before use.

**Secrets.** Patterns for well-known credential shapes:

- OpenAI-style and Anthropic-style keys: `sk-[A-Za-z0-9_-]{20,}`
- GitHub tokens: `gh[pousr]_[A-Za-z0-9]{36,}` and `github_pat_[A-Za-z0-9_]{50,}`
- AWS access key ids: `(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])`
- Slack tokens: `xox[baprs]-[A-Za-z0-9-]{10,}`
- JSON Web Tokens: `eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}`
- PEM private keys: from `-----BEGIN [A-Z ]*PRIVATE KEY-----` to the matching
  `-----END [A-Z ]*PRIVATE KEY-----`, across lines (`re.DOTALL`, non-greedy)

## Tests

Use the shared test data table in `docs/plan/README.md`. Build secret-shaped test values at run
time (see the note there).

`test_checksums.py`
- every valid vector in the table returns `True`, every invalid one `False`, for its function.
- each function returns `False` (does not raise) for `""`, `"abc"`, and a 100-character string.
- CIF with a letter as control character: `P2800000H` is valid (control value 8 → `H`),
  `P2800000J` is not.
- property: for any 8-digit number, appending the computed DNI letter validates and any other
  letter does not.
- property: `luhn_valid` is `True` for exactly one final digit given any 15-digit prefix.

`test_rules.py`
- each valid vector, embedded in a sentence (`"Contact: {value}."`), yields exactly one span of
  the right type whose surface (`text[start:end]`) equals the value, without the trailing dot.
- each invalid vector yields no span of that type.
- a text with one email, one IBAN and one phone yields three spans with correct offsets.
- `ES9121000418450200051332 IS MINE` detects only the IBAN (trailing-word case).
- `"order 12345678 Zebra"` yields no `NATIONAL_ID`: the letter after the digits starts a word.
- `enabled_types={"EMAIL"}` returns only emails for a text containing several types.
- `URL` is not detected by default and is detected when enabled; `https://example.com/a?b=1.` 
  excludes the final dot.
- unknown type in `enabled_types` raises `ValueError`.
- a PEM block spanning three lines is one `SECRET` span.
- determinism: calling `detect` twice returns equal lists.
- property: for arbitrary text, every returned span satisfies
  `0 <= start < end <= len(text)`.
- Spanish and English sentences containing no identifiers produce no spans (write five of each;
  include years, prices like `1.250,00 €`, and dates like `12/05/2024`).

## Acceptance checklist

- [ ] All shared vectors behave as specified.
- [ ] `detect` never raises on arbitrary text (property test).
- [ ] No secret-shaped literal is present in the source tree.
- [ ] `phonenumbers` is the only new dependency.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Names, organisations, addresses (slices 04 and 09).
- Overlap resolution between rules (slice 03).
- Identifiers of other countries. Add them later, one rule plus vectors each.
- Dates and money amounts. They are deliberately not pseudonymised (threat model, risk 6).

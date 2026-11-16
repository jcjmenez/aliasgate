# Slice 01: core types, placeholder codec, mapping

## Goal

The vocabulary of the core exists as typed, tested Python: spans, the detector protocol, the
placeholder format with its tolerant pattern, key normalisation and the per-request mapping.
No detection and no replacement yet.

## Depends on

Slice 00.

## Dependencies to add

None.

## Files

| File | Action |
|---|---|
| `src/aliasgate/types.py` | create |
| `src/aliasgate/placeholders.py` | create |
| `src/aliasgate/mapping.py` | create |
| `src/aliasgate/terms.py` | create |
| `tests/test_types.py`, `tests/test_placeholders.py`, `tests/test_mapping.py`, `tests/test_terms.py` | create |

## Specification

Read architecture §2, §4.2, §5.1 and §5.2 first.

### `types.py`

```python
PRIORITY_LITERAL: Final = 100
PRIORITY_DICTIONARY: Final = 80
PRIORITY_RULE: Final = 60
PRIORITY_ML: Final = 40
PRIORITY_PROPAGATION: Final = 20

MAX_ENTITY_TYPE_LENGTH: Final = 40

def validate_entity_type(name: str) -> str:
    """Return `name` if it matches [A-Z][A-Z_]* and is at most 40 chars; else raise ValueError."""

@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int
    entity_type: str
    score: float
    source: str
    priority: int

class Detector(Protocol):
    name: str
    def detect(self, text: str) -> list[Span]: ...
```

`Span.__post_init__` raises `ValueError` when `start < 0`, `end < start`, `score` is outside
`[0, 1]`, or the entity type is invalid. An empty span (`start == end`) is allowed at
construction; the resolver drops it later. The error message must not include any text content.

Entity types are plain strings, not an enum, because users define their own. Provide the
built-in names as module constants (`PERSON = "PERSON"`, …) for the list in architecture §2.

### `placeholders.py`

```python
MAX_PLACEHOLDER_LENGTH: Final = 64     # canonical placeholders
MAX_TOLERANT_LENGTH: Final = 128       # any match of TOLERANT

TOLERANT: Final[re.Pattern[str]]  # exactly the pattern in architecture §5.2, copied verbatim

def format_placeholder(entity_type: str, number: int) -> str:
    """Return the canonical placeholder, e.g. format_placeholder("PERSON", 1) == "[PERSON_1]"."""

def parse_match(match: re.Match[str]) -> tuple[str, int]:
    """Return (entity type, number) for a match of TOLERANT: take groups 1-2 or 3-4, whichever
    matched, remove backslashes from the type and upper-case it."""
```

`format_placeholder` validates the type, requires `number >= 1`, and raises `ValueError` if the
result would exceed `MAX_PLACEHOLDER_LENGTH`.

### `mapping.py`

```python
def normalize_key(surface: str) -> str:
    """NFKC, casefold, collapse whitespace runs to one space, strip."""

@dataclass(frozen=True, slots=True)
class Entity:
    key: str
    entity_type: str
    surface: str        # canonical surface: the first one seen
    placeholder: str

class Mapping:
    def assign(self, entity_type: str, surface: str, *, key: str | None = None) -> Entity:
        """Return the entity for the key (default: normalize_key(surface)), creating it with the
        next number for `entity_type` if the key is new. For an existing key the stored entity
        is returned unchanged, even if `entity_type` or the surface's case differ."""
    def get(self, key: str) -> Entity | None: ...
    def lookup(self, entity_type: str, number: int) -> str | None:
        """Return the canonical surface for a placeholder, or None."""
    def entities(self) -> tuple[Entity, ...]:
        """All entities in order of assignment."""
    def __len__(self) -> int: ...
```

- Numbers start at 1 and are counted per entity type.
- `assign` raises `ValueError` for a surface whose key is empty.
- `Mapping.__repr__` must not reveal surfaces: return `Mapping(entities=<count>)`. Likewise
  `Entity.__repr__` shows only the type and the placeholder. This protects against accidental
  logging (AGENTS.md rule 2).
- `Mapping` is not thread-safe and does not need to be: one instance per request.

### `terms.py`

```python
def build_term_pattern(surface: str) -> re.Pattern[str]:
    """Pattern matching `surface` as a whole term, case-insensitively, with any whitespace run
    between its tokens (architecture §4.3 step 3)."""
```

Split on whitespace, `re.escape` each token, join with `\s+`, wrap as
`(?<!\w)(?:...)(?!\w)`, compile with `re.IGNORECASE`. Raise `ValueError` for a surface with no
non-whitespace character.

## Tests

`test_types.py`
- valid span is constructed; each invalid field raises `ValueError`.
- `validate_entity_type` accepts `PERSON`, `CREDIT_CARD`; rejects `person`, `PERSON1`,
  `_PERSON`, `""`, a 41-character name.

`test_placeholders.py`
- `format_placeholder("PERSON", 1) == "[PERSON_1]"`; number 0 and negative raise.
- `TOLERANT` fully matches each of: `[PERSON_1]`, `[person_1]`, `[Person_12]`, `[PERSON 1]`,
  `[ PERSON_1 ]`, `\[PERSON_1\]`, `[CREDIT_CARD_3]`, `[PERSON\_1]`, `[CREDIT\_CARD\_3]`.
- `TOLERANT.search` finds nothing in: `[PERSON]`, `[1]`, `[PERSON_]`, `PERSON_1`, `[PERSON-1]`,
  `[PER SON_1]`, `[PERSON_01234567890]` (ten or more digits).
- in `\[PERSON_1]` (only the opening bracket escaped) the match is `[PERSON_1]`, without the
  backslash.
- `parse_match` returns `("CREDIT_CARD", 3)` for `[credit_card_3]` and for
  `[credit\_card\_3]`.
- no match is longer than `MAX_TOLERANT_LENGTH`: assert it for the longest constructible match
  (escaped brackets, two tabs each side, a 40-letter type with every possible escaped
  underscore, nine digits).
- property: for any valid type and any number in `1..10**6`,
  `parse_match(TOLERANT.fullmatch(format_placeholder(t, n))) == (t, n)`.

`test_mapping.py`
- `normalize_key("  Juan   PÉREZ ") == "juan pérez"`; full-width and ligature forms collapse
  under NFKC (`"ＡＣＭＥ"` and `"ACME"` share a key).
- first `PERSON` gets `[PERSON_1]`, second distinct `PERSON` gets `[PERSON_2]`, first `ORG` gets
  `[ORG_1]`.
- assigning `"ACME"` then `"acme"` returns the same entity; canonical surface stays `"ACME"`.
- assigning the same key with a different type returns the first entity.
- explicit `key=`: two surfaces assigned with different explicit keys get different entities
  even when `normalize_key` would merge them.
- `lookup("PERSON", 1)` returns the surface; `lookup("PERSON", 99)` returns `None`.
- `repr(mapping)` and `repr(entity)` do not contain the surface.
- property: after any sequence of `assign` calls, placeholders are unique and
  `lookup(*parse(placeholder))` returns the canonical surface of that entity.

`test_terms.py`
- pattern for `"Juan Pérez"` matches `Juan Pérez`, `JUAN  PÉREZ`, `juan\npérez`.
- it does not match inside `xJuan Pérez` or `Juan Pérezx`; it matches in `(Juan Pérez)` and
  `Juan Pérez.`.
- a surface with regex metacharacters (`"A+B (C)"`) is matched literally.
- whitespace-only surface raises.

## Acceptance checklist

- [ ] The four modules exist with exactly the public names above.
- [ ] No module in this slice imports anything outside the standard library.
- [ ] `repr` of `Mapping` and `Entity` reveal no surface.
- [ ] Property tests pass with Hypothesis' default settings.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Any detector, including the placeholder-literal detector (slice 03).
- Replacing or restoring text (slice 03).
- Persistence or serialisation of the mapping. It must not be picklable-to-disk by design; do
  not add `to_json` or similar.

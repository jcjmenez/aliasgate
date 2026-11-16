# Slice 03: pseudonymisation engine and restoration

## Goal

Given a list of texts and a list of detectors, the engine returns pseudonymised texts and a
mapping, and `restore` turns placeholders back into the original values. The round trip is
proven with property tests. This is the heart of the project.

## Depends on

Slice 01. (Slice 02 is not required: tests use a fake detector.)

## Dependencies to add

None.

## Files

| File | Action |
|---|---|
| `src/aliasgate/resolve.py` | create |
| `src/aliasgate/detectors/literal.py` | create (create `detectors/__init__.py` if slice 02 is not merged) |
| `src/aliasgate/engine.py` | create |
| `tests/fakes.py` | create |
| `tests/test_resolve.py`, `tests/detectors/test_literal.py`, `tests/test_engine.py`, `tests/test_restore.py` | create |

## Specification

Read architecture §3.3, §4 and §5 completely before starting. They define the algorithms; this
file defines the interfaces and the tests.

### `resolve.py`

```python
def resolve_overlaps(spans: Iterable[Span]) -> list[Span]:
    """Return non-overlapping spans sorted by start, chosen as in architecture §3.3."""
```

### `detectors/literal.py`

```python
class LiteralDetector:
    name = "literal"
    def detect(self, text: str) -> list[Span]:
        """One span of type LITERAL, priority 100, score 1.0, per match of TOLERANT."""
```

### `engine.py`

```python
@dataclass(frozen=True, slots=True)
class PseudonymizedBatch:
    texts: tuple[str, ...]
    mapping: Mapping

class DetectionError(Exception):
    """A detector failed. Carries the detector name; never carries text."""

class Pseudonymizer:
    def __init__(self, detectors: Sequence[Detector], *, allow_list: Iterable[str] = (),
                 propagate: bool = True, min_propagation_length: int = 3) -> None: ...
    def pseudonymize(self, texts: Sequence[str]) -> PseudonymizedBatch: ...

def restore(text: str, mapping: Mapping, *, json_escape: bool = False) -> str: ...

def count_unresolved(text: str, mapping: Mapping) -> int:
    """Number of TOLERANT matches in `text` that are not in the mapping."""
```

Behaviour details beyond the architecture:

- The constructor always prepends a `LiteralDetector`. Callers cannot remove it.
- `allow_list` entries are stored as keys (`normalize_key`). A span is dropped when
  `normalize_key(surface)` is in the allow list. `LITERAL` spans are never dropped.
- A span whose surface has an empty key (whitespace only) is dropped.
- `LITERAL` spans are assigned with an exact key: `mapping.assign("LITERAL", surface,
  key="\x00literal:" + surface)`. They are excluded from propagation (architecture §4.4).
- If any detector raises, `pseudonymize` raises `DetectionError(detector.name)` chained from the
  original exception (`raise ... from exc`). No partial result is returned.
- A detector returning a span outside `0 <= start <= end <= len(text)` is a detector bug:
  raise `DetectionError`.
- An empty text is returned as is. An empty batch returns an empty tuple and an empty mapping.
- `restore` with a mapping of zero entities returns the text unchanged (still valid).
- `restore` performs a single left-to-right pass with `TOLERANT.sub`. Restored values are not
  rescanned, so a surface that itself looks like a placeholder is safe.
- Add `Mapping.is_literal(entity)` or compare `entity.entity_type == "LITERAL"`; either is fine,
  but use one way consistently.

### `tests/fakes.py`

```python
class WordListDetector:
    """Test detector: reports every whole-word, case-sensitive occurrence of the given words."""
    def __init__(self, words: Mapping[str, str], *, name: str = "fake",
                 priority: int = PRIORITY_ML, score: float = 0.9) -> None: ...   # word -> type

class FailingDetector:
    """Test detector whose detect() always raises RuntimeError."""
```

Case-sensitive on purpose: it lets tests simulate a detector that misses a variant, which is
what propagation is for.

## Tests

`test_resolve.py`
- no spans → empty list; one span → that span.
- two disjoint spans are both kept, sorted by start.
- overlapping: higher priority wins regardless of score and length.
- same priority: higher score wins. Same priority and score: longer wins. Full tie: earlier
  start wins.
- adjacent spans (`a.end == b.start`) do not overlap.
- zero-length spans are dropped.
- property: output is sorted, pairwise non-overlapping, and a subset of the input.
- property: the highest-priority span of the input is always in the output.

`test_literal.py`
- `"see [PERSON_1] and [org 2]"` yields two `LITERAL` spans with correct offsets.
- plain text yields nothing.

`test_engine.py` (use `WordListDetector({"Alice": "PERSON", "Bob": "PERSON", "ACME": "ORG"})`)
- `["Alice met Bob at ACME."]` → `["[PERSON_1] met [PERSON_2] at [ORG_1]."]`.
- the same value twice in one text gets the same placeholder.
- the same value in two texts gets the same placeholder; numbering follows batch order.
- propagation: `["Alice called.", "alice called again."]` → second text becomes
  `"[PERSON_1] called again."` although the fake detector did not report the lower-case form.
- propagation respects word boundaries: `"Alice and Alicent"` leaves `Alicent` untouched.
- propagation disabled: the lower-case `alice` stays.
- `min_propagation_length`: a detected two-character surface is replaced where detected and not
  propagated.
- longest-first propagation: with detected entities `"Juan Pérez García"` (text 0) and
  `"Juan Pérez"` (text 1), a third text `"juan pérez garcía"` gets the placeholder of the longer
  entity.
- allow list: `allow_list=["ACME"]` leaves `ACME` and `acme` in place.
- placeholder literal: input `"Use [PERSON_1] for Alice"` → `"Use [LITERAL_1] for [PERSON_1]"`,
  and restoring gives back the input exactly.
- literals are exact: `"[PERSON_1] and [person_1]"` produces two different `LITERAL`
  placeholders and restores byte for byte.
- backslash before an entity: `"\\Alice"` (one backslash, then the name) round-trips exactly.
- `FailingDetector` → `DetectionError` whose `str()` contains the detector name and none of the
  input text.
- out-of-range span from a detector → `DetectionError`.
- empty batch and empty strings.
- determinism: two calls with the same input give equal results.
- property (round trip): for texts generated from an alphabet that includes the fake words,
  brackets, digits, underscores, spaces and newlines, with propagation disabled,
  `[restore(t, b.mapping) for t in b.texts] == list(texts)`.
- property (round trip with propagation): the same, compared after applying
  `normalize_key`-insensitive equality: `restore(...)` equals the original where every
  case-variant of a detected word is replaced by its canonical surface. Implement the oracle
  simply (use `build_term_pattern` per entity, longest first) and keep it in the test file.
- property (no residue): after pseudonymising, no canonical surface with key length ≥ 3 is
  matched by its `build_term_pattern` in any output text.
- property (append stability): for batches `A` and `A + B`, every entity of `mapping(A)` appears
  in `mapping(A + B)` with the same placeholder and surface; with `propagate=False` the first
  `len(A)` output texts are equal.

`test_restore.py`
- tolerant forms `[person_1]`, `[PERSON 1]`, `\[PERSON_1\]` are restored.
- unknown placeholder `[PERSON_9]` is left unchanged and `count_unresolved` returns 1.
- `json_escape=True`: a surface containing `"` and a newline is inserted as `\"` and `\n`, so
  that `json.loads('"' + restored + '"')` succeeds and contains the original surface.
- a surface that equals `[PERSON_2]` (via the literal path) is not restored twice.

## Acceptance checklist

- [ ] All four properties (round trip ×2, no residue, append stability) pass with at least 200
      Hypothesis examples each.
- [ ] `engine.py` imports only from the standard library and from `aliasgate.types`,
      `placeholders`, `mapping`, `terms`, `resolve`, `detectors.literal`.
- [ ] `DetectionError` messages never contain input text.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Streaming restoration (slice 07).
- JSON payloads (slice 05).
- Detection caching (slice 09).
- Linking different surfaces of the same real-world entity ("Juan Pérez" and "Mr. Pérez"). Each
  distinct key is its own entity in v1. See the backlog.

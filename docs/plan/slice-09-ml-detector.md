# Slice 09: ML detector (GLiNER) and detection cache

## Goal

Names of people, organisations and addresses that no rule or dictionary knows are detected by a
local model. The model is an optional install, loads at start-up, and its results are cached so
that resent conversation history costs nothing.

## Depends on

Slice 04.

## Dependencies to add

An optional extra, not a core dependency:

```toml
[project.optional-dependencies]
ml = ["gliner>=0.2"]
```

```bash
uv add --optional ml gliner
```

Check the latest `gliner` release and its Python version support first. If it does not support
one of the Python versions in the CI matrix, keep the core matrix as it is and run `ml` tests on
a supported version only.

## Before you start

1. Read the `gliner` package documentation for loading a model and predicting entities. When
   this plan was written the API was `GLiNER.from_pretrained(model_id)` and
   `model.predict_entities(text, labels, threshold=...)`, returning dicts with `start`, `end`,
   `text`, `label`, `score`. Confirm it.
2. Choose the default model. Candidates and what is known about them are in
   [state-of-the-art.md §4.3](../state-of-the-art.md). Requirements: multilingual (at least
   English and Spanish), a licence that permits commercial use and redistribution (verify on the
   model card; do not assume), runs on CPU. Record the model id, its licence and its revision
   hash in the pull request, and pin the revision in the default configuration.
3. If no candidate meets the licence requirement, stop and report.

## Files

| File | Action |
|---|---|
| `src/aliasgate/detectors/cache.py` | create |
| `src/aliasgate/detectors/chunking.py` | create |
| `src/aliasgate/detectors/gliner.py` | create |
| `src/aliasgate/config.py` | change: complete the `ml` section |
| `src/aliasgate/factory.py` | change: wire the ML detector, remove the `NotImplementedError` |
| `tests/detectors/test_cache.py`, `tests/detectors/test_chunking.py`, `tests/detectors/test_gliner.py`, `tests/detectors/test_gliner_real.py` | create |

## Specification

### Configuration

```yaml
detection:
  ml:
    enabled: false
    model: <model id chosen above>
    revision: <pinned revision>
    threshold: 0.4            # recall-oriented on purpose
    labels:                   # model label -> entity type
      person: PERSON
      organization: ORG
      address: ADDRESS
    window_words: 250
    overlap_words: 50
    cache_entries: 4096
```

`enabled: true` without the `ml` extra installed must fail at start-up with a message that
names the install command. It must never start and silently skip ML.

### `chunking.py`

Models have a limited input length. Long texts are cut into overlapping windows.

```python
@dataclass(frozen=True, slots=True)
class Window:
    start: int     # character offset in the original text
    end: int

def windows(text: str, *, window_words: int, overlap_words: int) -> list[Window]:
    """Cover the text with windows of at most `window_words` whitespace-separated words, each
    overlapping the previous one by `overlap_words` words. Windows start and end on word
    boundaries. A text with at most `window_words` words yields one window covering it all."""
```

Requires `0 <= overlap_words < window_words`. Empty or whitespace-only text yields no window.

### `gliner.py`

```python
class EntityModel(Protocol):
    def predict_entities(self, text: str, labels: list[str], threshold: float) -> list[dict[str, Any]]: ...

class GlinerDetector:
    name = "gliner"
    def __init__(self, model: EntityModel, *, labels: Mapping[str, str], threshold: float,
                 window_words: int = 250, overlap_words: int = 50) -> None: ...
    def detect(self, text: str) -> list[Span]: ...
    @property
    def fingerprint(self) -> str: ...

def load_gliner(model_id: str, revision: str | None) -> EntityModel:
    """Import gliner lazily and load the model. Raise a clear error if the extra is missing."""
```

`detect`:

1. For each window, call `predict_entities(text[w.start:w.end], list(labels), threshold)`.
2. Convert each result to a `Span` with offsets shifted by `w.start`, type `labels[label]`,
   `source="gliner"`, `priority=PRIORITY_ML`, score clamped to `[0, 1]`. Ignore results whose
   label is not in `labels`.
3. Trim leading and trailing whitespace from each span by adjusting the offsets. Drop spans
   shorter than 2 characters after trimming.
4. Deduplicate spans from overlapping windows: identical `(start, end, entity_type)` keep the
   highest score. Different spans that overlap are all returned; the resolver decides.
5. Return sorted by `(start, end)`.

The model object is injected, so unit tests use a fake and never import `gliner`.

Thread safety: guard `predict_entities` calls with a `threading.Lock` owned by the detector.
Model inference is not guaranteed thread-safe and one inference at a time is the simple, correct
choice for v1.

`fingerprint` is a stable string of the model id, revision, threshold, sorted labels and window
settings.

### `cache.py`

```python
class CachedDetector:
    def __init__(self, inner: Detector, *, fingerprint: str, max_entries: int) -> None: ...
    name: str                      # same as inner.name
    def detect(self, text: str) -> list[Span]: ...
    def stats(self) -> tuple[int, int]: ...   # (hits, misses)
```

- Key: `hashlib.sha256((fingerprint + "\x00" + text).encode()).digest()`. The cache stores the
  digest and the spans, never the text.
- LRU eviction with `collections.OrderedDict`, guarded by a lock. Return a new list on each call
  (spans are immutable, lists are not).
- Exceptions from the inner detector are not cached.
- `max_entries == 0` disables caching.

### Factory

When `ml.enabled`: load the model at start-up, wrap the detector in `CachedDetector`, and append
it after the rules detector. Run one warm-up prediction on a fixed synthetic sentence so the
first real request is not slow and a broken model fails at start-up.

## Tests

`test_chunking.py`
- short text → one window covering everything.
- 600 words with window 250 and overlap 50 → windows start at words 0, 200, 400; every window
  boundary is a word boundary; the union covers the text.
- invalid parameters raise.
- property: every non-whitespace character of the text is inside at least one window, and every
  pair of consecutive windows shares exactly `overlap_words` words unless the last window is
  shorter.

`test_gliner.py` (with a fake `EntityModel` that returns scripted results per input)
- offsets are shifted correctly for the second window.
- an entity reported by two overlapping windows appears once, with the higher score.
- labels are mapped; unknown labels are ignored.
- leading and trailing spaces are trimmed; a one-character result is dropped.
- the fake raising → the exception propagates (the engine turns it into `DetectionError`).
- `fingerprint` changes when the threshold, labels or model id change.
- the detector takes its lock around inference (use a fake that records concurrent entry from
  two threads and assert it never sees two at once).

`test_cache.py`
- second call with the same text hits; `stats()` reflects it.
- different fingerprint → different entry.
- eviction order is least recently used.
- returned lists are independent (mutating one does not affect the next call).
- exceptions are not cached.
- the cache's internal state contains no input text (inspect keys and values).

`test_gliner_real.py` (all marked `@pytest.mark.ml`; not run in default CI)
- the real model loads and detects `Juan Pérez` as PERSON and `ACME Corporation` as ORG in one
  English and one Spanish sentence.
- a 3,000-word synthetic text is processed without error.
- detection of the same text twice is identical.

`test_factory.py` additions
- `ml.enabled: true` with the extra missing (simulate by patching the import) → a start-up error
  naming the install command.
- with a fake model injected, dictionary beats ML and rules beat ML on overlapping spans.

## Acceptance checklist

- [ ] Model id, licence and pinned revision recorded in the pull request and in the example
      configuration.
- [ ] Core install (`uv sync` without extras) does not import `gliner` or `torch` anywhere.
- [ ] Default CI does not download a model. `uv run pytest -m ml` passes locally; say so in the
      pull request with the measured latency for a 200-word text on your machine.
- [ ] `enabled: true` without the extra fails at start-up.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Fine-tuning or training a model.
- GPU support, ONNX export, batching across requests (backlog, after measurements).
- Alternative backends (Presidio, OpenAI Privacy Filter). The `EntityModel` protocol and the
  `Detector` protocol are the extension points; adding a backend is a separate change.
- Language detection.

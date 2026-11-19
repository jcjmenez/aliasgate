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

State after slice 04: `MLSettings` in `config.py` already has `enabled`, `model`, `threshold`
(validated in `[0, 1]`) and `labels` (values must be valid entity types), with
`extra="forbid"`; add the new fields there. `load_settings` deep-merges mappings, so `labels`
set both in the YAML file and in `ALIASGATE_DETECTION__ML__LABELS` are merged key by key.
Decide whether the environment should replace the whole mapping instead; either way, test it.
`build_pseudonymizer` raises `NotImplementedError` when `ml.enabled`; replace that with the
real wiring and remove its test.

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

### Decisions made while implementing (slice 09)

- Checked on 2026-11-19. `gliner` 0.2.30 (PyPI, 2026-10-31, Python >= 3.10) installs torch
  2.15.0 and transformers 5.22.2. Its API is as described:
  `GLiNER.from_pretrained(model_id, revision=...)` and
  `predict_entities(text, labels, flat_ner=True, threshold=0.5, ...)`, which returns dicts with
  `start`, `end`, `text`, `label` and `score`. `threshold` is the fourth parameter, after
  `flat_ner`, so it is always passed by keyword.
- Model: `urchade/gliner_multi_pii-v1` at revision `1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d`.
  The model card says Apache-2.0 and covers en, fr, de, es, pt and it. Its training set
  (`urchade/synthetic-pii-ner-mistral-v1`) is Apache-2.0 and its base encoder
  (`microsoft/mdeberta-v3-base`) is MIT. GLiNER2 models such as
  `fastino/gliner2-privacy-filter-PII-multi` need the separate `gliner2` package, so they were
  not candidates. Loading the model also fetches the config and tokenizer of
  `microsoft/mdeberta-v3-base` at its default branch. The revision pin does not cover that
  download.
- The `ml` extra also lists `protobuf`, decided with the maintainer. With transformers 5.x,
  converting mdeberta's sentencepiece tokenizer needs it, and without it the model does not
  load.
- Windows count GLiNER's own word unit, decided with the maintainer. That unit is
  `\w+(?:[-_]\w+)*|\S`: a run of word characters, or one punctuation mark. Whitespace-separated
  words, as first specified, would leak. GLiNER keeps only the first `max_len` (384) of its
  words and drops the rest with nothing but a warning. A text of 206 whitespace-separated words
  of CSV (1,408 GLiNER words, so one window) lost the names at its end. Counting the model's
  unit makes `window_words` a real bound. `load_gliner` returns an adapter that refuses any
  text over the model's `max_len`, so a truncation is an error and never a silent miss. The
  warm-up runs one whole window, so a `window_words` the model cannot take stops the gateway at
  start-up.
- `GlinerDetector.__init__` takes `model_id` (keyword, required) and `revision` (keyword,
  default `None`). The fingerprint must name the model, and the model object does not know its
  own id.
- `EntityModel.predict_entities` returns `list[dict[str, object]]`, not `dict[str, Any]`,
  because AGENTS.md allows no `Any` in core modules. Every result is checked. Offsets must be
  integers (not booleans) inside the window and the score must be a number, or detection
  raises. That is fail closed. Results whose label is not configured are ignored. Spans with
  the same offsets but different types are sorted by entity type, so the output does not
  depend on the model's order.
- The detector validates its settings at construction: at least one non-empty label, valid
  entity types, a threshold in `[0, 1]`, and `0 <= overlap_words < window_words`.
- The cache encodes the key with `"surrogatepass"`. JSON allows unpaired surrogate escapes,
  plain UTF-8 refuses them, and the encoding stays injective. A failed detection counts as a
  miss. With `max_entries == 0` every call is a miss.
- Configuration: `model` and `revision` are non-empty strings, and `revision` may be `null`
  for a local model directory. `labels` needs at least one entry, with non-empty keys. Sections
  still merge key by key, file then environment. Any other value is replaced, and that now
  includes mappings: `ALIASGATE_DETECTION__ML__LABELS` replaces the file's `labels` and does not
  merge with it, so the environment can also remove a label. `model: null` is no longer valid,
  and the example configuration shows the new defaults.
- Measured on 2026-11-19 on an Apple M5 Pro CPU (torch 2.15.0, no GPU). The default settings
  take 140 ms (median of 10) for 200 words and 2.6 s for 3,000 words. Building the pseudonymizer
  takes 7.1 s with the model already downloaded, warm-up included. A cache hit takes 0.3 ms.
  One loaded model uses about 1.5 GB of resident memory. The model over-predicts. In a Spanish
  business text it labelled "La reunión con el cliente" as a person. That is accepted, since
  recall matters more.

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

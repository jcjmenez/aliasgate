# Slice 11: evaluation harness

## Goal

Detection quality is a measured number, per entity type and per language, produced by one
command. CI fails if the deterministic layers regress. Operators can run the same command on
their own labelled samples.

## Depends on

Slice 04. (Works without slice 09; reports the ML layer when it is installed.)

## Dependencies to add

```bash
uv add --dev faker
```

## Files

| File | Action |
|---|---|
| `src/aliasgate/eval/__init__.py`, `corpus.py`, `metrics.py`, `report.py`, `__main__.py` | create |
| `tools/generate_corpus.py` | create |
| `tests/eval/corpus/synthetic.en.jsonl`, `tests/eval/corpus/synthetic.es.jsonl` | create (generated, committed) |
| `tests/eval/test_metrics.py`, `tests/eval/test_corpus.py`, `tests/eval/test_recall_gate.py` | create |
| `docs/evaluation.md` | create |

The `aliasgate.eval` package must not import `faker`; only the generator tool does.

## Specification

### Corpus format (`corpus.py`)

JSON Lines, one example per line:

```json
{"id": "en-0001", "lang": "en", "text": "…", "spans": [{"start": 10, "end": 20, "type": "PERSON"}]}
```

```python
@dataclass(frozen=True, slots=True)
class Example:
    id: str
    lang: str
    text: str
    spans: tuple[GoldSpan, ...]

def load_corpus(path: Path) -> list[Example]:
    """Validate offsets (in range, non-overlapping, sorted) and entity types; raise on error."""
```

### Generator (`tools/generate_corpus.py`)

Deterministic: a fixed seed produces byte-identical files. For each language (`en`, `es`):

- At least 40 sentence templates per language, written by hand, covering the situations the
  gateway will meet: support tickets, emails, contract clauses, CRM notes, chat messages, log
  lines, CSV-like rows, JSON snippets. Templates contain slots such as `{person}`, `{org}`,
  `{email}`, `{phone}`, `{iban}`, `{card}`, `{national_id}`, `{tax_id}`, `{ip}`, `{address}`.
- Slot values come from `Faker(locale)` with `Faker.seed(...)`, except identifiers with
  checksums, which you generate yourself with the checksum functions so they are valid: Spanish
  DNI, NIE, CIF; IBANs for ES, DE, GB, FR; Luhn-valid card numbers.
- Formatting variety for identifiers: IBAN with and without spaces, cards with spaces, hyphens
  or none, phones in national and international format, DNI with and without hyphen, upper and
  lower case.
- At least 15% of examples contain **no** entity (to measure false positives), with tricky
  content: years, prices, dates, order numbers, version numbers, ISBN-like digit groups.
- At least 10% of examples repeat an entity in a different letter case (to exercise
  propagation).
- 500 examples per language.

The generated files are committed so tests do not depend on the Faker version. The generator
has a `--check` flag that regenerates in memory and reports whether the committed files differ
(not run in CI, since Faker data changes between versions).

### Metrics (`metrics.py`)

```python
@dataclass(frozen=True, slots=True)
class TypeScore:
    entity_type: str
    gold: int
    predicted: int
    exact_tp: int          # same start, end and type
    overlap_tp: int        # any character overlap, any type
    @property
    def recall_exact(self) -> float: ...
    @property
    def recall_overlap(self) -> float: ...
    @property
    def precision_exact(self) -> float: ...

@dataclass(frozen=True, slots=True)
class Report:
    by_type: tuple[TypeScore, ...]
    leaked_chars: int      # characters of gold spans still present after pseudonymisation
    gold_chars: int
    round_trip_failures: int
    @property
    def leak_rate(self) -> float: ...     # leaked_chars / gold_chars

def evaluate(pseudonymizer: Pseudonymizer, examples: Sequence[Example]) -> Report: ...
```

- Evaluate the **whole engine** (detectors, resolution, propagation), one example per batch.
  Predicted spans are recovered by comparing against the engine's internal final spans: add a
  method `Pseudonymizer.analyze(texts) -> list[list[Span]]` that returns the final spans per
  text without replacing. `pseudonymize` must be implemented on top of it so both cannot drift.
- **Leak rate is the headline number.** It is computed from characters: for each gold span,
  count the characters not covered by any predicted span. It answers "how much sensitive text
  got through", independent of type confusion and boundary conventions.
- `recall_overlap` ignores the type on purpose: a person detected as an organisation is still
  protected.
- `round_trip_failures`: examples where `restore(pseudonymize(text))` differs from the text
  after replacing case variants by canonical surfaces (reuse the oracle from slice 03's tests,
  moved to a shared helper).
- Division by zero yields `1.0` for recall when there is no gold span and for precision when
  nothing is predicted. State this in the docstrings.

### Command (`__main__.py`)

```bash
uv run python -m aliasgate.eval --config examples/aliasgate.example.yaml tests/eval/corpus/*.jsonl
```

Options: `--format table|json` (default table), `--max-leak-rate FLOAT` (exit code 1 if the
leak rate is above it), `--types EMAIL,IBAN` to restrict. The output contains numbers and entity
types only, never example text. With `--show-misses N` it prints up to N missed spans with their
example id and offsets (not the text), so a developer can look them up in the corpus.

### CI gate (`test_recall_gate.py`)

With the default configuration (rules only, no dictionary, no ML), on both committed corpora:

- `recall_overlap >= 0.99` for each of `EMAIL`, `IBAN`, `CREDIT_CARD`, `NATIONAL_ID`, `TAX_ID`,
  `IP_ADDRESS`;
- `recall_overlap >= 0.95` for `PHONE`;
- on the examples without entities, at most 2% of examples have any predicted span;
- `round_trip_failures == 0`.

If the implementation of slice 02 does not reach a threshold, fix the rule, not the threshold.
If you believe a threshold is wrong for a stated reason, stop and report.

A second test, marked `ml`, runs with the ML detector enabled and **prints** the report for
`PERSON`, `ORG` and `ADDRESS` without asserting thresholds. Copy its output into
`docs/evaluation.md` as the first published baseline, with the model id, revision and date.

### `docs/evaluation.md`

Explains the metrics in plain terms, how to run the command, how to write a corpus from your own
(carefully handled) samples, the baseline numbers, and a clear statement that synthetic-corpus
numbers are an upper bound on real-world performance.

## Tests

`test_metrics.py`
- a perfect detector (fake that returns the gold spans) → recall 1, precision 1, leak rate 0.
- a detector that returns nothing → recall 0, leak rate 1.
- a detector that covers half of one gold span → `overlap_tp` 1, `exact_tp` 0, leak rate equals
  the uncovered fraction.
- wrong type with exact boundaries → counted in `overlap_tp`, not in `exact_tp`.
- empty corpus and corpus without gold spans do not divide by zero.

`test_corpus.py`
- both committed files load, have 500 examples each and satisfy the proportions above.
- every gold span's text is non-empty and has no leading or trailing whitespace.
- every gold identifier with a checksum validates with the functions of slice 02.
- a file with an out-of-range span fails to load with the example id in the message.

`test_recall_gate.py` as specified.

## Acceptance checklist

- [ ] Corpora are generated by the committed tool with a fixed seed and are committed.
- [ ] All corpus content is synthetic.
- [ ] `pseudonymize` is built on `analyze`.
- [ ] The CI gate passes without lowering any threshold.
- [ ] `docs/evaluation.md` exists; ML baseline included if slice 09 is merged, otherwise a
      placeholder section saying how to produce it.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Public datasets (ai4privacy and similar). Their licences and content need review first; see
  the backlog.
- Measuring the effect on LLM answer quality (backlog: utility evaluation).
- Hyper-parameter search for thresholds.

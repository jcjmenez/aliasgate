# Evaluation

The evaluation harness measures how much sensitive text the gateway lets through. It runs the
whole engine (every detector, overlap resolution and propagation) on labelled examples, built
from a configuration exactly as the gateway builds it, and reports numbers per entity type and
per language. The same command runs in CI on synthetic corpora and on your own samples.

**Numbers on the synthetic corpora are an upper bound.** The synthetic texts were written for
the detectors' formats, their values are clean and their contexts are short. Real tickets,
emails and documents contain typos, unusual formats, names no list knows and identifiers of
other countries. Expect lower numbers on real data, and measure them on your own samples
before you rely on the gateway.

## What the numbers mean

Each example is pseudonymised on its own, as one request. A gold span is a range of the text
that a person labelled with an entity type. A predicted span is a range the engine replaced.

| Number | Meaning |
|---|---|
| **leak rate** | The share of gold characters that no predicted span covers, whatever its type. This is the headline number: it answers "how much sensitive text reached the provider". A name detected only in part leaks the rest. |
| `recall_overlap` | The share of gold spans that some predicted span touches, of any type. A person detected as an organisation is still protected, so the type is ignored on purpose. |
| `recall_exact` | The share of gold spans predicted with the same start, end and type. |
| `precision_exact` | The share of predicted spans of a type that match a gold span exactly. Low precision means over-pseudonymisation: lost utility, not a leak. |
| examples without entities, with a predicted span | Examples with no gold span at all in which the engine replaced something: false alarms on text such as dates, prices, versions or order numbers. |
| round-trip failures | Examples where restoring the pseudonymised text does not give the original back, apart from the documented change of case and whitespace variants to the canonical surface of their entity (architecture §5.3). It should always be 0. |

A ratio without a denominator is 1.0 for recall (no gold span of the type) and for precision
(nothing of the type predicted), and 0.0 for the leak rate (no gold characters).

With `--types`, only gold and predicted spans of the listed types are scored, but a predicted
span of any type still covers gold characters. "Examples without entities" then means without
gold spans of those types.

### Linking

When gold spans carry entity ids (see [Your own corpus](#your-own-corpus)), a second table,
`linking (pairs of mentions within an example)`, scores whether the mentions of one entity
share a placeholder number and whether the mentions of two entities do not. It counts pairs of
gold spans of one type within one example; pairs never cross examples, because each example is
one request.

The **family** of a placeholder is its type and number, without the variant letter:
`[PERSON_1]` and `[PERSON_1_B]` are one family. A gold span takes the family of the predicted
span that covers most of its characters, the earlier one on a tie. A gold span that no
predicted span touches has no family: its characters leaked, and the leak rate counts them.

| Number | Meaning |
|---|---|
| `mentions` | Gold spans of the type. |
| `placed` | Of them, the ones with a family. |
| `same_pairs` | Pairs of placed mentions of one entity. |
| `linked` | Of those, the pairs in one family. |
| `wrong` | Pairs of placed mentions of two entities in one family: a wrong merge. It is worse than a missed link, because the model then reasons about two people as one. |
| `link_recall` | `linked / same_pairs`: how many pairs that should share a number do. |
| `link_precision` | `linked / (linked + wrong)`: how many pairs that share a number should. |

Both ratios are 1.0 without a denominator. Only the types with at least one entity id get a
row, and a span without an id is an entity of its own. Linking says nothing about mentions
that leaked, since they are not placed: read it together with the leak rate. `--show-wrong-merges
N` lists up to `N` wrong merges after the missed spans, as
`{example_id} {start}..{end} and {start}..{end} {TYPE}`.

## Running it

```bash
uv run python -m aliasgate.eval --config examples/aliasgate.example.yaml tests/eval/corpus/synthetic.*.jsonl
```

Without `--config`, the default configuration applies (rules only, no dictionary, no ML).
`ALIASGATE_*` environment variables override the configuration file, as for the gateway. With
`detection.ml.enabled`, the ML extra must be installed (`uv sync --extra ml`); the model is
loaded once for all files.

| Option | Effect |
|---|---|
| `--format table\|json` | Output format; `table` by default. JSON has one object per language (see below). |
| `--max-leak-rate RATE` | Exit with code 1 if the leak rate of any language is above `RATE` (0 to 1). |
| `--types EMAIL,IBAN` | Score only these entity types. |
| `--show-misses N` | List up to `N` gold spans that are not fully covered, by example id, offsets and type. |
| `--show-wrong-merges N` | List up to `N` wrong merges (two entities in one placeholder family), by example id, offsets and type. |

Exit codes: 0, 1 when a leak rate is above the limit, 2 for invalid options, an invalid
configuration or corpus, or a detector failure.

Each language object of the JSON output holds the numbers of the table (`examples`,
`leak_rate`, `leaked_chars`, `gold_chars`, `round_trip_failures`,
`examples_without_entities`, `flagged_without_entities`), `by_type` (one object per type row),
`missed_spans` (the total) and `misses` (up to `--show-misses`), and for linking `linking` (one
object per linking row, with `entity_type`, `mentions`, `placed`, `same_pairs`,
`linked_pairs`, `wrong_pairs`, `link_recall` and `link_precision`), `wrong_merge_pairs` (the
total) and `wrong_merges` (up to `--show-wrong-merges`, each with `example_id`, `first` and
`second` as `[start, end]`, and `entity_type`). Without entity ids, `linking` and
`wrong_merges` are empty and `wrong_merge_pairs` is 0.

The output holds numbers, entity types, example ids and offsets only, never example text or
entity ids. Errors name the file, the line, the example id and span numbers. To see a missed
span or a wrong merge, look it up in the corpus by its id and offsets.

## The CI gate

`tests/eval/test_recall_gate.py` runs the default configuration (rules only) on both synthetic
corpora and requires, for each language:

- `recall_overlap` of at least 0.99 for `EMAIL`, `IBAN`, `CREDIT_CARD`, `NATIONAL_ID`, `TAX_ID`
  and `IP_ADDRESS`, and at least 0.95 for `PHONE`;
- at most 2% of the examples without entities with a predicted span;
- no round-trip failure.

If a change to a rule breaks the gate, fix the rule, not the threshold. Changes that touch
detection should include before and after numbers from this command (see
[CONTRIBUTING.md](../CONTRIBUTING.md)).

## Your own corpus

A corpus is a JSON Lines file, UTF-8, one example per line:

```json
{"id": "tickets-0001", "lang": "es", "text": "Hola, soy Juan Pérez, mi DNI es 12345678Z.", "spans": [{"start": 10, "end": 20, "type": "PERSON"}, {"start": 32, "end": 41, "type": "NATIONAL_ID"}]}
{"id": "tickets-0002", "lang": "es", "text": "Juan Pérez llamó. El Sr. Pérez pide la factura.", "spans": [{"start": 0, "end": 10, "type": "PERSON", "entity": "p1"}, {"start": 25, "end": 30, "type": "PERSON", "entity": "p1"}]}
```

- `id`: unique in the file (and across the files of one run). Reports refer to examples by it.
- `lang`: any non-empty label; the report has one section per value.
- `spans`: sorted by `start`, not overlapping, each non-empty. `start` and `end` are Python
  string offsets (code points, not bytes), as `text[start:end]` uses them. `type` is an entity
  type such as `PERSON`, `ORG`, `ADDRESS`, `EMAIL`, `PHONE`, `IBAN`, `CREDIT_CARD`,
  `NATIONAL_ID`, `TAX_ID`, `IP_ADDRESS`, or one of your own (upper-case letters and
  underscores).
- `entity` (optional): the entity the span mentions, to measure [linking](#linking). 1 to 64
  ASCII letters, digits, `_`, `.` or `-`, scoped to its example: spans of one example with the
  same id are mentions of one entity, and they must have the same type. A span without an id
  is an entity of its own. The harness never prints an id, and its errors name span numbers
  instead, because an id may be made from the value it labels (`juan-perez`). Prefer neutral
  ids such as `p1`.

The loader rejects a file with a malformed line and says which line and example it is.

Label everything that must not reach the provider, and nothing else: leave surrounding spaces
and punctuation outside the span, and label each occurrence of a repeated value. Draw the
examples from the traffic the gateway will carry (the same channels, languages and lengths),
including messages without any sensitive value, so that false alarms are measured too. A few
hundred examples per language give useful numbers; differences of a point or two between runs
of a small corpus are noise.

**Handle real samples as the data they are.** A labelled corpus of real messages is a
concentrated copy of personal data. Keep it on a machine that is allowed to hold the original
data, outside this repository and any shared drive, restrict who can read it, and delete it
when the measurement is done. Do not paste examples into issues or chats; the harness output
is safe to share because it holds only numbers, types and ids.

## The synthetic corpora

`tests/eval/corpus/synthetic.en.jsonl` and `synthetic.es.jsonl` hold 500 examples each. They
are written by `tools/generate_corpus.py` from 47 hand-written templates per language
(support tickets, emails, contract clauses, CRM notes, chat messages, log lines, CSV rows and
JSON snippets) plus 15 templates without entities (dates, prices, versions, order numbers,
ISBNs). 17% of the examples contain no entity, and 12% (English) and 14% (Spanish) repeat an
entity in another letter case, to exercise propagation.

Every value is synthetic. Names, companies and addresses come from
[Faker](https://faker.readthedocs.io/) with a fixed seed. Identifiers with a checksum (Spanish
DNI, NIE and CIF, IBANs of Spain, Germany, the United Kingdom and France, card numbers) are
random digits completed with the checksum functions of the rule detector, and US SSNs avoid
the never-issued ranges. Emails use `example.com` and `example.org`, IP addresses come from
documentation and private ranges, and US and UK phone numbers from the ranges reserved for
fiction (555-0100 to 555-0199, and 020 7946 0xxx). Spanish phone numbers are random valid
numbers. Identifiers vary in format: IBANs with and without spaces, cards with spaces,
hyphens or none, phones in national and international format, DNI and NIE with and without
hyphens, upper and lower case.

The files are committed, so tests do not depend on the Faker version. To regenerate them, or
to check that the committed files match the generator (only with the Faker version in
`uv.lock`, which was 40.54.0 when they were written):

```bash
uv run python tools/generate_corpus.py
uv run python tools/generate_corpus.py --check
```

## The person corpus

`tests/eval/corpus/persons.en.jsonl` and `persons.es.jsonl` measure the shorter forms of
person names: a first name, a surname, a title with a surname, part of a Spanish name with two
surnames. Each holds 280 examples, 20 in each of 14 categories, written by
`tools/generate_person_corpus.py` from two hand-written templates per category and language
(tickets, emails, CRM notes, chat messages). Example ids are `{lang}-{category}-{nnn}`. Every
mention of a person is a `PERSON` span with the person's id (`p1`, `p2`, in order of first
mention). Titles, a possessive `'s` and punctuation stay outside the span, and a name used as
a common word (`the brown box`, `la mesa`) is not a span.

| Category | What its examples contain |
|---|---|
| `full` | Full names only. |
| `first`, `last` | A full name, later its first name, or its surname (in Spanish the first surname), alone. |
| `title` | A full name, later a title and the surname (`Ms. Johnson`, `la Sra. Romero`). |
| `before` | The first name or the surname alone, then the full name. |
| `run` | Several words of the name: in English the middle and last names, and the first and last names; in Spanish the first name and the first surname, and both surnames. |
| `possessive` | The surname with `'s`; in Spanish between parentheses or before a comma. |
| `shared-last`, `shared-first` | Two persons with the same surname or the same first name. Later each one by the word they do not share, and the shared word once, with a referent the text makes clear. |
| `other` | A full name, later another person named only by the first word of that first name. |
| `word` | A surname that is also a common word (`Brown`, `Mesa`), once alone as the name and once in lower case as the word. |
| `sentence` | A first name that is also a common word (`Will`, `Rosa`), alone as the name and at the start of a sentence as the word. |
| `lower` | A full name, later a shorter form in lower case, as in a quick chat. |
| `org` | A person, and an organisation named after the surname (an `ORG` span with id `o1`). |

The rules do not look for names, and the ML model does not run in CI. So the generator also
writes `tests/eval/corpus/persons.terms.yaml`, a dictionary with every full name of both
files, and `tests/eval/corpus/persons.yaml` is a configuration that loads it. With it, CI
measures the case that matters here: the full name is detected, and the question is what
happens to its shorter forms.

```bash
uv run python -m aliasgate.eval --config tests/eval/corpus/persons.yaml tests/eval/corpus/persons.*.jsonl
```

The names come from [Faker](https://faker.readthedocs.io/) (`en_US` and `es_ES`, seeds 1201
and 1202) and from fixed lists of names that are also common words. A gender is drawn for each
person so that titles and articles agree with the name. A name is drawn again when one of its
words is shorter than three characters, is not made of letters, or is a title or a particle;
when the persons of an example share a word their category does not ask for; when a name word
also occurs outside the gold spans of its example (except the common word of `word` and
`sentence`); and when a shorter form of several words anywhere in the corpus contains a full
name of the dictionary, which would then detect it.

The files are committed. To regenerate them, or to check that the committed files match the
generator (only with the Faker version in `uv.lock`, which was 40.54.0 when they were
written):

```bash
uv run python tools/generate_person_corpus.py
uv run python tools/generate_person_corpus.py --check
```

## Baseline

Measured on 2026-11-20 with the committed corpora.

### Rules only (the default configuration)

These are the CI gate's numbers. The rules do not look for names, organisations or addresses,
so those leak completely and dominate the leak rate: 0.4450 in English (9,455 of 21,247 gold
characters) and 0.5238 in Spanish (12,738 of 24,318).

| Type | en: gold | en: `recall_overlap` | en: `recall_exact` | en: `precision_exact` | es: gold | es: `recall_overlap` | es: `recall_exact` | es: `precision_exact` |
|---|---|---|---|---|---|---|---|---|
| `EMAIL` | 175 | 1.000 | 1.000 | 1.000 | 177 | 1.000 | 1.000 | 1.000 |
| `PHONE` | 97 | 1.000 | 1.000 | 1.000 | 97 | 1.000 | 1.000 | 1.000 |
| `IBAN` | 108 | 1.000 | 1.000 | 1.000 | 106 | 1.000 | 1.000 | 1.000 |
| `CREDIT_CARD` | 54 | 1.000 | 1.000 | 1.000 | 54 | 1.000 | 1.000 | 1.000 |
| `NATIONAL_ID` | 60 | 1.000 | 1.000 | 1.000 | 62 | 1.000 | 1.000 | 1.000 |
| `TAX_ID` | 44 | 1.000 | 1.000 | 1.000 | 44 | 1.000 | 1.000 | 1.000 |
| `IP_ADDRESS` | 72 | 1.000 | 1.000 | 1.000 | 72 | 1.000 | 1.000 | 1.000 |
| `PERSON` | 267 | 0.000 | 0.000 | – | 266 | 0.000 | 0.000 | – |
| `ORG` | 201 | 0.000 | 0.000 | – | 204 | 0.000 | 0.000 | – |
| `ADDRESS` | 61 | 0.000 | 0.000 | – | 60 | 0.000 | 0.000 | – |

No example without entities had a predicted span (0 of 85 in each language), and there was no
round-trip failure. Re-measured on 2026-11-21 after slice 14: the 16 English US SSNs whose nine
digits also form a valid Spanish number, replaced as `PHONE` until then, are now
`NATIONAL_ID`, which raised English `NATIONAL_ID` `recall_exact` from 0.733 and English
`PHONE` `precision_exact` from 0.858 to 1.000. No other number changed.

### ML layer

Model `urchade/gliner_multi_pii-v1` at revision `1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d`
(Apache-2.0), with the default ML settings (threshold 0.4, labels `person`, `organization`
and `address`), on top of the rules. Scored for `PERSON`, `ORG` and `ADDRESS` only, with
gliner 0.2.30, transformers 5.22.2 and torch 2.15.0 on an Apple M5 Pro CPU. Both corpora took
48 seconds, model loading included.

| Type | en: gold | en: predicted | en: `recall_overlap` | en: `recall_exact` | en: `precision_exact` | es: gold | es: predicted | es: `recall_overlap` | es: `recall_exact` | es: `precision_exact` |
|---|---|---|---|---|---|---|---|---|---|---|
| `PERSON` | 267 | 380 | 1.000 | 1.000 | 0.703 | 266 | 315 | 1.000 | 0.992 | 0.838 |
| `ORG` | 201 | 165 | 0.930 | 0.567 | 0.691 | 204 | 212 | 0.971 | 0.838 | 0.807 |
| `ADDRESS` | 61 | 71 | 1.000 | 0.984 | 0.845 | 60 | 65 | 0.983 | 0.967 | 0.892 |

Leak rate for these three types: 0.0360 in English (340 of 9,455 characters) and 0.0224 in
Spanish (285 of 12,738). The model over-predicts, as the precision column shows: 30 of the 182
English examples without a name, organisation or address had a predicted span of one of those
types (16.5%), and 21 of 182 in Spanish (11.5%). Exact recall for English `ORG` is low because
company names made of several surnames ("X, Y and Z") or of two hyphenated surnames are often
found only in part: 53 of 76 and 26 of 66 of them were not matched exactly.

To reproduce this table:

```bash
uv sync --extra ml
uv run pytest -m ml tests/eval/test_recall_gate.py
```

### Person corpus

Measured on 2026-11-22 with the committed person corpus, before any linking of shorter forms.
These are the numbers that person name linking must improve.

**With the dictionary of full names** (`--config tests/eval/corpus/persons.yaml`, what CI
runs). The dictionary finds the 380 full-name mentions of each language and nothing else. Every
shorter form leaks: the leak rate is 0.3625 in English (2,851 of 7,865 gold characters) and
0.2735 in Spanish (2,967 of 10,848). Of those, `PERSON` accounts for 2,547 of 7,561 characters
in English and 2,646 of 10,527 in Spanish, and the 20 organisation names of `org`, which the
dictionary does not hold, for the rest. There was no round-trip failure.

| Type | en: gold | en: predicted | en: `recall_overlap` | en: `recall_exact` | en: `precision_exact` | es: gold | es: predicted | es: `recall_overlap` | es: `recall_exact` | es: `precision_exact` |
|---|---|---|---|---|---|---|---|---|---|---|
| `PERSON` | 770 | 380 | 0.494 | 0.494 | 1.000 | 770 | 380 | 0.494 | 0.494 | 1.000 |
| `ORG` | 20 | 0 | 0.000 | 0.000 | – | 20 | 0 | 0.000 | 0.000 | – |

| Type | Language | `mentions` | `placed` | `same_pairs` | `linked` | `wrong` | `link_recall` | `link_precision` |
|---|---|---|---|---|---|---|---|---|
| `PERSON` | en | 770 | 380 | 20 | 20 | 0 | 1.000 | 1.000 |
| `PERSON` | es | 770 | 380 | 20 | 20 | 0 | 1.000 | 1.000 |
| `ORG` | en | 20 | 0 | 0 | 0 | 0 | 1.000 | 1.000 |
| `ORG` | es | 20 | 0 | 0 | 0 | 0 | 1.000 | 1.000 |

The perfect linking numbers say only that the shorter forms are not placed: the 20 pairs are the
full names that `full` repeats. In every category but `full`, every shorter form is forwarded
unchanged, which the leak rate and `recall_overlap` show.

**With the ML layer, without the dictionary** (`ALIASGATE_DETECTION__ML__ENABLED=true`,
`--types PERSON,ORG`), same model, revision, settings and library versions as above. Both
files took 30 seconds, model loading included. The leak rate is 0.2768 in English (2,177 of
7,865 characters, all of them `PERSON`) and 0.2300 in Spanish (2,495 of 10,848), with no
round-trip failure.

| Type | en: gold | en: predicted | en: `recall_overlap` | en: `recall_exact` | en: `precision_exact` | es: gold | es: predicted | es: `recall_overlap` | es: `recall_exact` | es: `precision_exact` |
|---|---|---|---|---|---|---|---|---|---|---|
| `PERSON` | 770 | 424 | 0.551 | 0.513 | 0.932 | 770 | 397 | 0.516 | 0.504 | 0.977 |
| `ORG` | 20 | 123 | 1.000 | 1.000 | 0.163 | 20 | 116 | 1.000 | 1.000 | 0.172 |

| Type | Language | `mentions` | `placed` | `same_pairs` | `linked` | `wrong` | `link_recall` | `link_precision` |
|---|---|---|---|---|---|---|---|---|
| `PERSON` | en | 770 | 424 | 77 | 34 | 0 | 0.442 | 1.000 |
| `PERSON` | es | 770 | 397 | 36 | 21 | 0 | 0.583 | 1.000 |
| `ORG` | en | 20 | 20 | 0 | 0 | 0 | 1.000 | 1.000 |
| `ORG` | es | 20 | 20 | 0 | 0 | 0 | 1.000 | 1.000 |

The model finds a few shorter forms, mostly in `run`, `title` and `shared-last`, but it gives
most of them a number of their own: of the 57 English and 16 Spanish pairs beyond those of
`full`, 14 and 1 share a number. It finds no standalone first name in `first` and no surname
in `last`, and it over-predicts organisations.

To reproduce these numbers:

```bash
uv run python -m aliasgate.eval --config tests/eval/corpus/persons.yaml tests/eval/corpus/persons.*.jsonl
uv sync --extra ml
ALIASGATE_DETECTION__ML__ENABLED=true uv run python -m aliasgate.eval --types PERSON,ORG tests/eval/corpus/persons.*.jsonl
```

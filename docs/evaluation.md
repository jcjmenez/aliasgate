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

## Running it

```bash
uv run python -m aliasgate.eval --config examples/aliasgate.example.yaml tests/eval/corpus/*.jsonl
```

Without `--config`, the default configuration applies (rules only, no dictionary, no ML).
`ALIASGATE_*` environment variables override the configuration file, as for the gateway. With
`detection.ml.enabled`, the ML extra must be installed (`uv sync --extra ml`); the model is
loaded once for all files.

| Option | Effect |
|---|---|
| `--format table\|json` | Output format; `table` by default. JSON has one object per language. |
| `--max-leak-rate RATE` | Exit with code 1 if the leak rate of any language is above `RATE` (0 to 1). |
| `--types EMAIL,IBAN` | Score only these entity types. |
| `--show-misses N` | List up to `N` gold spans that are not fully covered, by example id, offsets and type. |

Exit codes: 0, 1 when a leak rate is above the limit, 2 for invalid options, an invalid
configuration or corpus, or a detector failure.

The output holds numbers, entity types and example ids only, never example text. Errors name
the file, the line and the example id. To see a missed span, look it up in the corpus by its
id and offsets.

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
```

- `id`: unique in the file (and across the files of one run). Reports refer to examples by it.
- `lang`: any non-empty label; the report has one section per value.
- `spans`: sorted by `start`, not overlapping, each non-empty. `start` and `end` are Python
  string offsets (code points, not bytes), as `text[start:end]` uses them. `type` is an entity
  type such as `PERSON`, `ORG`, `ADDRESS`, `EMAIL`, `PHONE`, `IBAN`, `CREDIT_CARD`,
  `NATIONAL_ID`, `TAX_ID`, `IP_ADDRESS`, or one of your own (upper-case letters and
  underscores).

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

## Baseline

Measured on 2026-11-20 with the committed corpora.

### Rules only (the default configuration)

These are the CI gate's numbers. The rules do not look for names, organisations or addresses,
so those leak completely and dominate the leak rate: 0.4450 in English (9,455 of 21,247 gold
characters) and 0.5238 in Spanish (12,738 of 24,318).

| Type | en: gold | en: `recall_overlap` | en: `recall_exact` | en: `precision_exact` | es: gold | es: `recall_overlap` | es: `recall_exact` | es: `precision_exact` |
|---|---|---|---|---|---|---|---|---|
| `EMAIL` | 175 | 1.000 | 1.000 | 1.000 | 177 | 1.000 | 1.000 | 1.000 |
| `PHONE` | 97 | 1.000 | 1.000 | 0.858 | 97 | 1.000 | 1.000 | 1.000 |
| `IBAN` | 108 | 1.000 | 1.000 | 1.000 | 106 | 1.000 | 1.000 | 1.000 |
| `CREDIT_CARD` | 54 | 1.000 | 1.000 | 1.000 | 54 | 1.000 | 1.000 | 1.000 |
| `NATIONAL_ID` | 60 | 1.000 | 0.733 | 1.000 | 62 | 1.000 | 1.000 | 1.000 |
| `TAX_ID` | 44 | 1.000 | 1.000 | 1.000 | 44 | 1.000 | 1.000 | 1.000 |
| `IP_ADDRESS` | 72 | 1.000 | 1.000 | 1.000 | 72 | 1.000 | 1.000 | 1.000 |
| `PERSON` | 267 | 0.000 | 0.000 | – | 266 | 0.000 | 0.000 | – |
| `ORG` | 201 | 0.000 | 0.000 | – | 204 | 0.000 | 0.000 | – |
| `ADDRESS` | 61 | 0.000 | 0.000 | – | 60 | 0.000 | 0.000 | – |

No example without entities had a predicted span (0 of 85 in each language), and there was no
round-trip failure. In English, 16 US SSNs are replaced as `PHONE`, because their nine digits
also form a valid Spanish number: they are covered, but under the wrong type (backlog §9).

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

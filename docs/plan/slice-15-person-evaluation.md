# Slice 15: person mentions evaluation set

## Goal

The evaluation harness measures two things it cannot see today: whether the mentions of one
person share a placeholder number, and whether the mentions of two different people do not. A
new synthetic corpus of person mentions in English and Spanish contains full names together with
the shorter forms people use: a first name, a surname, a title with a surname, part of a
two-surname Spanish name. It also holds the cases where linking those forms would go wrong. The
corpus labels which mentions name the same person.

The slice changes no detection and no placeholder. It records today's numbers as the baseline
that [slice 16](slice-16-person-tokens.md) must improve, and it shows a leak that the existing
corpora miss. With full names detected, every shorter form is sent to the provider unchanged,
because neither the detectors nor propagation find it. In the existing corpora, none of the 533
gold `PERSON` spans is a shorter form of another one, so this gap has never been measured.

This is the first of two slices for backlog §1, part 2 (linking person names).

## Depends on

Slice 14 (v0.3.0 released). Every earlier slice is merged.

## Decisions taken with the maintainer before implementation (2026-11-21)

1. **Two slices, specified together.** This slice builds the evaluation set and the metrics.
   Slice 16 builds the linking and gates it on this corpus. Rejected: one slice for both, which
   would be a large pull request that changes behaviour before it has been measured.
2. **Gold entity ids.** A gold span may name its entity with an id, scoped to its example. Spans
   with the same id are mentions of one entity. The corpus format stays compatible: corpora
   without ids load and score as before.
3. **Pairwise link scores, by placeholder family.** The family of a placeholder is its type and
   number. The variant letter is ignored, so `[PERSON_1]` and `[PERSON_1_B]` are one family. Two
   mentions of one gold entity in one family are a link. Two mentions of different gold
   entities in one family are a wrong merge. Wrong merges are worse than missed links, because
   the model then reasons about two people as one.
4. **CI finds full names with a dictionary.** The rules do not detect persons, and the ML model
   does not run in CI. The generator therefore writes a dictionary with every full name of the
   corpus. With it, CI measures exactly the case slice 16 is about: the full name is detected,
   and the shorter forms are not. The ML numbers are measured locally and recorded.

## Found while specifying (prototype of 2026-11-21, ML on, synthetic texts)

`urchade/gliner_multi_pii-v1` with the default ML settings found `Alice Johnson`, but not
`Ms. Johnson` or `Alice` later in the same text. It found `Juan Pérez García` but not
`Sr. Pérez` or `Juan`. It found `Carlos Méndez` and `Lucía Romero Vidal`, but not `Lucía` in
`Hola Lucía` or `Carlos` in `Saludos, Carlos`. Every one of these shorter forms was sent
unchanged. In other contexts the model did find some short forms (`Sra. Romero Vidal` as one
span in one text, `Pérez` alone in another). It then gave them a number unrelated to the full
name.

## Decisions made while implementing

Four questions were asked on 2026-11-22, and the maintainer chose the recommended option each
time:

1. **`other` and shared words.** The test list said that persons share no word outside
   `shared-last` and `shared-first`, but in `other` the second person is named by a word of the
   first one's name. The test asserts that in `other` the second person is named by one word,
   the first word of the first person's first mention, and that this is the only word they
   share. In the 11 remaining categories, persons share none.
2. **What the dictionary holds.** The test list said "the first mention of every person", but
   in `before` the first mention is a shorter form, and the second person of `other` has no full
   name. Putting those in the dictionary would detect the shorter forms. The dictionary holds
   the first full-name mention of every person who has one, as the format section says, and
   the test compares the whole list, in order.
3. **Gender.** `title` needs `Ms.`/`Mr.` and `la Sra.`/`el Sr.` to agree with the name, which
   `first_name()` does not tell. The generator draws a gender for each person and uses Faker's
   `first_name_female()` or `first_name_male()` (the same lists, split). The listed first names
   of `sentence` carry their own gender. Spanish titles include their article, so Spanish
   templates use them only inside a sentence. The rest of the templates are gender-neutral.
4. **Collisions.** The dictionary of full names applies to every example. The generator draws
   a person again when a shorter form of several words, anywhere in both corpora, contains a
   full name (or the other way round), and when a name word occurs outside the gold spans of
   its example, apart from the listed word of `word` and `sentence`. Neither rule rejected a
   drawing of the committed corpus, so unit tests force both, and tests on the committed files
   check both properties.

Other choices:

- **Templates.** Slots are `{a}`, `{b}` (full names), `{a.first}`, `{a.last}`,
  `{a.middle}`, `{a.last2}`, `{a.first_last}`, `{a.middle_last}`, `{a.surnames}`, an
  optional `|lower`, `{a.title}` (outside the span), `{org}` and `{common}`. The examples of
  a category alternate between its two templates (`001` is the first, `002` the second).
  `full`, the second templates of `first` and `last`, and the shared and `other` categories
  name two persons. Spanish templates avoid `y` and `o` before a name, which become `e` and `u`
  before some names. In English `run`, the full name includes the middle name. The common-word
  uses (`the brown box`, `Will you check it?`) are a fixed phrase per listed word and template.
  Templates contain no value the rules detect, so the gate of slice 16 on `word` sees only names.
- **Families.** Predicted spans of any type give a gold span its family, as they cover it. A
  placeholder literal, or a span whose entity is not in the mapping, has none. Wrong merges are
  listed only for the types that get a `LinkScore`, so a corpus without ids has none.
- **The existing glob.** `docs/evaluation.md` and `CONTRIBUTING.md` ran the harness on
  `tests/eval/corpus/*.jsonl`, which now also matches the person corpus and would merge it into
  the same languages. Both now use `synthetic.*.jsonl`, and CONTRIBUTING also gives the person
  corpus command.
- **Tests beyond the list.** Two spans without an id are two entities (a mutation that
  compared `None == None` survived without it). Spans of different types never pair, pairs
  never cross examples, a placeholder literal has no family, valid ids of 64 characters, wrong
  merges ordered across examples. For the generator: two templates per category, determinism,
  name words of at least three letters that are no title or particle, a lower-case shorter form
  in `lower`, the organisation of `org`.
- **Measured.** Each rule was broken on purpose and a test failed: the variant letter, the tie
  rule, pairs of different types, wrong merges of types without ids, two spans without ids,
  propagated spans, the id length, ids shared across types, the linking table, and the three
  generator rules. The generator writes both corpora in under a second.

**Per category, with the dictionary** (`persons.yaml`, what CI runs). `placed` counts the full
names; every other mention leaked whole. No wrong pair anywhere, because only full names are
replaced:

| Category | en: mentions | en: placed | en: `PERSON` leaked chars | en: wrong pairs | es: mentions | es: placed | es: `PERSON` leaked chars | es: wrong pairs |
|---|---|---|---|---|---|---|---|---|
| `full` | 60 | 60 | 0 of 761 | 0 | 60 | 60 | 0 of 1270 | 0 |
| `first` | 60 | 30 | 186 of 583 | 0 | 60 | 30 | 198 of 812 | 0 |
| `last` | 60 | 30 | 187 of 592 | 0 | 60 | 30 | 176 of 797 | 0 |
| `title` | 40 | 20 | 131 of 392 | 0 | 40 | 20 | 121 of 535 | 0 |
| `before` | 40 | 20 | 118 of 381 | 0 | 40 | 20 | 119 of 543 | 0 |
| `run` | 60 | 20 | 497 of 878 | 0 | 60 | 20 | 531 of 946 | 0 |
| `possessive` | 40 | 20 | 125 of 391 | 0 | 40 | 20 | 125 of 556 | 0 |
| `shared-last` | 100 | 40 | 348 of 852 | 0 | 100 | 40 | 392 of 1240 | 0 |
| `shared-first` | 100 | 40 | 358 of 874 | 0 | 100 | 40 | 397 of 1264 | 0 |
| `other` | 50 | 20 | 181 of 446 | 0 | 50 | 20 | 182 of 576 | 0 |
| `word` | 40 | 20 | 93 of 321 | 0 | 40 | 20 | 85 of 473 | 0 |
| `sentence` | 40 | 20 | 75 of 299 | 0 | 40 | 20 | 66 of 416 | 0 |
| `lower` | 40 | 20 | 119 of 382 | 0 | 40 | 20 | 128 of 557 | 0 |
| `org` | 40 | 20 | 129 of 409 | 0 | 40 | 20 | 126 of 542 | 0 |

**Per category, with the ML layer and no dictionary** (for slice 16, not required). Pairs are
counted as `same_pairs` / `linked` / `wrong`. In English `run` the model covered only 6 of the
20 three-word names whole, and in Spanish it missed part of one full name in `first` and one in
`before`:

| Category | en: placed | en: `PERSON` leaked chars | en: same / linked / wrong | es: placed | es: `PERSON` leaked chars | es: same / linked / wrong |
|---|---|---|---|---|---|---|
| `full` | 60 | 0 | 20 / 20 / 0 | 60 | 0 | 20 / 20 / 0 |
| `first` | 30 | 186 | 0 / 0 / 0 | 30 | 206 | 0 / 0 / 0 |
| `last` | 30 | 187 | 0 / 0 / 0 | 30 | 176 | 0 / 0 / 0 |
| `title` | 28 | 79 | 8 / 0 / 0 | 20 | 121 | 0 / 0 / 0 |
| `before` | 20 | 118 | 0 / 0 / 0 | 20 | 130 | 0 / 0 / 0 |
| `run` | 49 | 221 | 42 / 14 / 0 | 28 | 418 | 8 / 0 / 0 |
| `possessive` | 20 | 125 | 0 / 0 / 0 | 20 | 125 | 0 / 0 / 0 |
| `shared-last` | 47 | 306 | 7 / 0 / 0 | 47 | 347 | 7 / 0 / 0 |
| `shared-first` | 40 | 358 | 0 / 0 / 0 | 40 | 397 | 0 / 0 / 0 |
| `other` | 20 | 181 | 0 / 0 / 0 | 22 | 170 | 1 / 1 / 0 |
| `word` | 20 | 93 | 0 / 0 / 0 | 20 | 85 | 0 / 0 / 0 |
| `sentence` | 20 | 75 | 0 / 0 / 0 | 20 | 66 | 0 / 0 / 0 |
| `lower` | 20 | 119 | 0 / 0 / 0 | 20 | 128 | 0 / 0 / 0 |
| `org` | 20 | 129 | 0 / 0 / 0 | 20 | 126 | 0 / 0 / 0 |

## Dependencies to add

None. Faker is already a development dependency (slice 11).

## Files

| File | Action |
|---|---|
| `src/aliasgate/eval/corpus.py` | change: optional `entity` on gold spans |
| `src/aliasgate/eval/metrics.py` | change: link scores and wrong merges |
| `src/aliasgate/eval/report.py` | change: linking in the table and in JSON |
| `src/aliasgate/eval/__main__.py` | change: `--show-wrong-merges` |
| `tools/generate_person_corpus.py` | new: the person corpus generator |
| `tests/eval/corpus/persons.en.jsonl`, `tests/eval/corpus/persons.es.jsonl` | new, generated |
| `tests/eval/corpus/persons.terms.yaml` | new, generated: the full names, as a dictionary |
| `tests/eval/corpus/persons.yaml` | new, hand-written: a configuration that loads that dictionary |
| `tests/eval/test_corpus.py`, `tests/eval/test_metrics.py`, `tests/eval/test_command.py` | change |
| `tests/eval/test_person_corpus.py` | new |
| `docs/evaluation.md` | change: entity ids, linking numbers, the person corpus, its baseline |
| `CHANGELOG.md` | change: an `Unreleased` → `Added` entry |

## Specification

Read [docs/evaluation.md](../evaluation.md) and `src/aliasgate/eval/` before starting. Nothing in
the core, the dialects or the proxy changes, and neither do the existing corpora or their
numbers.

### Corpus format (`eval/corpus.py`)

```python
@dataclass(frozen=True, slots=True)
class GoldSpan:
    start: int
    end: int
    entity_type: str
    entity: str | None = None  # gold entity id, scoped to its example
```

- A span may have the key `entity`. Its value is a string of 1 to 64 characters, each a letter,
  a digit, `_`, `.` or `-` (ASCII). Spans of one example with the same id are mentions of one
  entity. A span without an id is an entity of its own.
- Spans that share an id must have the same type.
- Errors follow the existing style and never repeat the id, because in a user's corpus an id
  may be made from the value:
  - `span 3 has an invalid entity id`;
  - `span 4 has the entity id of span 1 with another type`;
  - an unknown key: `span 2 must be an object with start, end and type, and optionally entity`.
- The existing corpora have no ids and load into the same `GoldSpan` values as before.

### Link scores (`eval/metrics.py`)

```python
@dataclass(frozen=True, slots=True)
class LinkScore:
    """Pairwise link counts for one entity type, within examples. Recall and precision are 1.0
    when their denominator is 0."""

    entity_type: str
    mentions: int      # gold spans of the type
    placed: int        # of them, the ones with a family
    same_pairs: int    # pairs of placed mentions of one gold entity
    linked_pairs: int  # of those, the pairs in one family
    wrong_pairs: int   # pairs of placed mentions of two gold entities in one family

    @property
    def link_recall(self) -> float: ...     # linked_pairs / same_pairs

    @property
    def link_precision(self) -> float: ...  # linked_pairs / (linked_pairs + wrong_pairs)


@dataclass(frozen=True, slots=True)
class WrongMerge:
    """Two gold mentions of different entities in one placeholder family: positions only."""

    example_id: str
    first: tuple[int, int]
    second: tuple[int, int]
    entity_type: str
```

- **Family of a predicted span.** Find the entity the span is replaced as, the way
  `expected_restoration` finds it: by key for a detected span, by the longest matching entity
  of its type for a propagated span. The family is the type and number of that entity's
  placeholder, read with `parse_match`. The variant letter is ignored. `LITERAL` spans have no
  family.
- **Family of a gold span.** The family of the predicted span that covers the most characters
  of it. On a tie, the predicted span that starts first wins. A gold span that no predicted
  span touches has no family. Its characters leaked, and the leak rate already counts them.
- **Pairs.** For every pair of gold spans of one type in one example, both with a family:
  - same gold entity, same family: a linked pair;
  - same gold entity, different families: counted in `same_pairs` only (a missed link);
  - different gold entities, same family: a wrong pair, also recorded as a `WrongMerge`.
  Spans of different types never form a pair. Examples are separate batches, so pairs never
  cross examples.
- **Which types are scored.** `Report.linking` has one `LinkScore` for each type that has at
  least one gold span with an entity id among the scored examples. With `types`, only those
  types. Sorted by type.
- `Report` gains `linking: tuple[LinkScore, ...]` and `wrong_merges: tuple[WrongMerge, ...]`.
  The wrong merges are ordered by example, then by first span, then by second span.
- Today, two mentions with the same key share one placeholder, so they are linked, or wrong
  when the gold says they are two people (`Smith` for two different people). That is the
  behaviour being measured, not an error of the metric.

### Output (`eval/report.py`, `eval/__main__.py`)

- **Table.** When `report.linking` is not empty, a second table follows the type table, with
  the columns `type`, `mentions`, `placed`, `same_pairs`, `linked`, `wrong`, `link_recall` and
  `link_precision`, the ratios with three decimals. Its heading line is
  `linking (pairs of mentions within an example):`.
- **`--show-wrong-merges N`** (an integer ≥ 0, default 0) lists up to `N` wrong merges after the
  misses, as `  {example_id} {start}..{end} and {start}..{end} {TYPE}`, under the line
  `wrong merges ({shown} of {total}):`.
- **JSON.** Each language object gains `"linking"` (a list of objects with the fields of
  `LinkScore` and both ratios), `"wrong_merge_pairs"` (the total) and `"wrong_merges"` (up to
  `N` objects with `example_id`, `first`, `second`, `entity_type`).
- The output still holds numbers, types, example ids and offsets only. Entity ids are never
  printed: in a user's corpus they may carry the value.
- Exit codes do not change. No new gate option.

### Person corpus (`tools/generate_person_corpus.py`)

```bash
uv run python tools/generate_person_corpus.py           # write the files
uv run python tools/generate_person_corpus.py --check   # exit 1 if the committed files differ
```

It writes `tests/eval/corpus/persons.en.jsonl`, `persons.es.jsonl` and `persons.terms.yaml`.
The tool is deterministic, like `tools/generate_corpus.py`: Faker with locales `en_US` and
`es_ES`, seeds 1201 and 1202, and a `random.Random` seeded the same way for every other choice.
The two generators share no code, so that changing one cannot change the other's files.

**Persons.**

- English: a first name and a surname, from `first_name()` and `last_name()`. Templates of the
  `run` category add a middle name (another `first_name()`).
- Spanish: a first name and two surnames. `first_name()` may give a compound first name such as
  `María José`, and both of its words are kept.
- Every word of a name has at least 3 characters and consists of letters, with at most one
  inner apostrophe or hyphen. A name is drawn again when a word breaks this rule or is one of
  the generator's titles or particles: `mr`, `mrs`, `ms`, `miss`, `dr`, `prof`, `sir`, `sr`,
  `sra`, `srta`, `don`, `doña`, `dña`, `dra`, `de`, `del`, `la`, `las`, `los`, `y`, `van`,
  `von`, `der`, `den`, `da`, `dos`, `san`, `santa`. Slice 16 defines the engine's lists; this
  generator keeps its own copy. Spanish surnames with a particle (`de la Fuente`) are
  therefore not drawn.
- The persons of one example share no name word and no full name, unless the category says
  so. On a collision, draw again.
- Some categories need names that are also common words. They take one name word from these
  fixed lists and the other words from Faker. English surnames: `Brown`, `Stone`, `King`,
  `Young`, `Rice`, `Bell`. English first names: `Will`, `May`, `Mark`. Spanish surnames: `Mesa`,
  `Plaza`, `Torre`, `Luna`, `Vega`, `Rubio`. Spanish first names: `Rosa`, `Luz`, `Paz`.

**Gold spans.**

- Every mention of a person is a `PERSON` span with the person's id: `p1`, `p2`, and so on, in
  order of first mention.
- A span covers the name only. Titles (`Ms.`, `la Sra.`), a possessive `'s` and punctuation
  stay outside.
- A name word used as a common word (`the brown box`, `la mesa`, `Will you send it?`) is not a
  gold span.
- In the `org` category, the organisation name is an `ORG` span with its own id (`o1`).

**Categories.** Every example id is `{lang}-{category}-{nnn}`, with `nnn` from `001` within its
category. Each category has two templates per language, and each template is rendered 10 times.
That gives 14 × 20 = 280 examples per language. The templates are written by hand, in the
voice of the existing corpora (tickets, emails, CRM notes, chat messages), with one to four
sentences. The examples below show what each category must contain. Write the templates
themselves, and vary them.

| Category | What the template contains | English example | Spanish example |
|---|---|---|---|
| `full` | Full names only, no shorter form | `Alice Johnson approved the refund for Bob Smith.` | `Lucía Romero Vidal aprobó el reembolso de Carlos Méndez Soto.` |
| `first` | A full name, later its first name alone | `Alice Johnson opened the ticket. Alice will call back tomorrow.` | `Lucía Romero Vidal abrió la incidencia. Lucía llamará mañana.` |
| `last` | A full name, later its surname alone (Spanish: the first surname) | `... Johnson confirmed the address.` | `... Romero confirmó la dirección.` |
| `title` | A full name, later a title and the surname | `... Ms. Johnson says the invoice is wrong.` | `... La Sra. Romero dice que la factura está mal.` |
| `before` | The first name or the surname alone, then the full name | `Alice called on Monday. Later, Alice Johnson sent the form.` | `Romero llamó el lunes. Después, Lucía Romero Vidal envió el formulario.` |
| `run` | English: a name with a middle name, later first and last name, and middle and last name. Spanish: later the first name with the first surname, and both surnames | `Alice Mary Johnson signed. Mary Johnson paid, and Alice Johnson left.` | `Juan Pérez García firmó. Juan Pérez pagó y Pérez García se fue.` |
| `possessive` | English: the surname with `'s`. Spanish: the surname between parentheses or before a comma | `... Johnson's report is attached.` | `... El informe (Romero) va adjunto.` |
| `shared-last` | Two persons with the same surname. Later each first name alone, and the shared surname once, with a referent made clear by the text | `Bob Smith, the owner, and Alice Smith, his daughter, called. Alice paid. The owner, Mr. Smith, asked for a receipt.` | `Juan Pérez García, el titular, y Ana Pérez Soto, su hija, llamaron. Ana pagó. El titular, el Sr. Pérez, pidió el recibo.` |
| `shared-first` | Two persons with the same first name. Later each surname alone, and the shared first name once, with a clear referent | `Alice Johnson and Alice Brown joined. Johnson leads the team. Alice, the new hire, starts Monday.` (gold: the new hire) | (same pattern) |
| `other` | A full name, later another person named only by a word of the first name | `Alice Johnson signed the contract. Alice from accounting, who is not related to her, will countersign.` | `Lucía Romero Vidal firmó. Lucía, la de contabilidad, que no tiene relación con ella, firmará también.` |
| `word` | A name word from the word lists, used once as the name alone and once in lower case as a common word | `Rose Brown sent the parcel. Brown says the brown box is damaged.` | `Pedro Mesa reservó la sala. Mesa dice que la mesa está rota.` |
| `sentence` | A first name from the word lists, alone as the name, and at the start of a sentence as a common word | `Will Stone sent the file. Will you check it? Will said it is urgent.` | `Rosa Vega envió el archivo. Rosa es el color de la portada. Rosa dice que es urgente.` |
| `lower` | A full name, later a shorter form in lower case, as in a quick chat | `Alice Johnson wrote earlier. thanks alice, got it` | `Lucía Romero Vidal escribió antes. gracias lucía, recibido` |
| `org` | A person, and an organisation whose name contains the person's surname | `Lucía Vidal works at Vidal Logistics. Vidal will send the quote.` | `Carlos Méndez trabaja en Méndez Asesores. Méndez enviará el presupuesto.` |

**`persons.terms.yaml`.** A dictionary file in the format of `load_terms`: every full name of
both corpora, as written in its first mention, with type `PERSON`. Each name appears once, in
order of first appearance, English first. Organisation names are not in it. The file starts
with a comment saying it is generated and synthetic.

**`persons.yaml`.** Written by hand, not generated:

```yaml
# Configuration for the person corpus: the dictionary finds every full name, so shorter forms
# are measured on their own. Synthetic values only.
detection:
  dictionary:
    files: [persons.terms.yaml]
```

### Baseline (measured in this slice, recorded in `docs/evaluation.md`)

Run both commands on the committed files and record every number of their output (leak rate,
`PERSON` and `ORG` rows, linking rows), per language:

```bash
uv run python -m aliasgate.eval --config tests/eval/corpus/persons.yaml tests/eval/corpus/persons.*.jsonl
ALIASGATE_DETECTION__ML__ENABLED=true uv run python -m aliasgate.eval --types PERSON,ORG tests/eval/corpus/persons.*.jsonl
```

The second one needs `uv sync --extra ml` and runs locally only. Also record the `PERSON` leaked
characters and the wrong pairs per category for the dictionary run. Compute them with a
throwaway script that groups examples by the category in their ids, and keep that script out of
the repository. With the dictionary, expect no leak in `full` and every shorter form leaked in
the other categories. If a number contradicts this expectation, report it in the pull request
before going on. Slice 16 needs the per-category numbers.

### Documentation

- `docs/evaluation.md`:
  - the `entity` key in "Your own corpus", with the reason ids are never printed;
  - the linking table, `--show-wrong-merges` and the JSON fields in "What the numbers mean" and
    in the options table;
  - a section "The person corpus": its categories, why CI uses the full-name dictionary, and
    how to regenerate and check it;
  - the baseline numbers under "Baseline".
- `CHANGELOG.md`: under `Unreleased`, `Added`: the linking scores, `--show-wrong-merges` and
  the person corpus.

## Tests

Use synthetic values only. The names in the examples above and in the shared test data are
enough.

`test_corpus.py`
- A span with `"entity": "p1"` loads with `entity == "p1"`. Without the key, `entity` is None.
- An empty id, a 65-character id, an id with a space or a non-ASCII letter, and a non-string id
  are each rejected with `invalid entity id`. The message contains none of these ids.
- Two spans with the same id and different types are rejected, and the message names both
  span numbers.
- An unknown span key is still rejected, with the new message.
- The committed `synthetic.*.jsonl` files load unchanged, and every span has `entity` None.

`test_metrics.py` (fake detectors from `tests/fakes.py`, with links where a variant is needed)
- Two mentions of one gold entity whose placeholders are `[PERSON_1]` and `[PERSON_1_B]` are
  a linked pair: the variant letter is ignored.
- Two mentions of one gold entity with `[PERSON_1]` and `[PERSON_2]` are a missed link:
  `same_pairs` 1, `linked_pairs` 0.
- Two gold entities whose mentions have the same key (`Smith` and `Smith`) share a
  placeholder, so they are one wrong pair. The `WrongMerge` holds the example id, both offsets
  and the type.
- A gold span that no predicted span touches is not placed, and forms no pair.
- A gold span covered by two predicted spans of different families takes the family of the one
  that covers more of it. On a tie, it takes the family of the earlier one.
- A gold span covered by a propagated span takes the family of the entity that propagation
  used.
- A type without any entity id gets no `LinkScore`. A type with ids gets one even when it has
  no pair.
- `types` restricts the linking scores as it restricts the type scores.
- Empty denominators give 1.0 for both ratios.
- Three mentions of one entity in one family give three linked pairs (all pairs counted).

`test_command.py`
- The table shows the linking table only for a corpus with entity ids, and JSON always has the
  three new fields (an empty list and 0 without ids).
- `--show-wrong-merges 1` lists one wrong merge by example id and offsets.
- Neither format contains an entity id or any text of the corpus: search the output of a run on
  a small corpus for every id and every gold surface.
- `--show-wrong-merges -1` exits with code 2.

`test_person_corpus.py`
- `--check` reports whether the files differ from a fresh generation, as the existing
  generator test does it: generate into a temporary directory, check (0), change one file and
  check (1), delete it and check (1). That the committed files match the generator is checked
  by running `--check` with the Faker version in `uv.lock` before committing them. Record that
  version in `docs/evaluation.md`, as for the existing corpora.
- Each language has 280 examples. Ids follow `{lang}-{category}-{nnn}`, and each of the 14
  categories has 20 examples.
- Every example has at least one `PERSON` span, and every `PERSON` span has an entity id. In
  every category but `lower`, every `PERSON` span starts with an upper-case letter. No `PERSON`
  span starts or ends with whitespace or punctuation, or includes a title.
- In `shared-last` and `shared-first`, two persons share a word. In every other category, the
  persons of one example share none.
- In `word` and `sentence`, the word from the list also occurs outside every gold span.
- `persons.terms.yaml` loads with `load_terms`, holds only `PERSON` terms, and contains the
  first mention of every person of both corpora.
- With `persons.yaml`, the harness reports no round-trip failure in either language, and every
  `PERSON` span of the `full` category is fully covered.

## Acceptance checklist

- [ ] The existing corpora, their numbers and the CI recall gate are unchanged.
- [ ] Entity ids are validated and never printed. A test searches the output for them.
- [ ] Link scores ignore the variant letter, and their definitions match this file.
- [ ] The person corpus has 14 categories × 20 examples per language and is reproducible with
      `--check`.
- [ ] The baseline is recorded in `docs/evaluation.md` for the dictionary run and the ML run,
      with the per-category numbers of the dictionary run in the pull request and in this file
      ("Decisions made while implementing").
- [ ] `CHANGELOG.md` and `docs/evaluation.md` are updated.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Any change to detection, linking, placeholders or the engine (slice 16).
- Examples made of several texts (conversation turns). Linking across texts is tested with unit
  tests in slice 16.
- A CI gate on link scores (slice 16).
- Public datasets (backlog §13).

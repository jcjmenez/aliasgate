# Slice 19: organisation name forms and linking

## Goal

Once an organisation's full name is detected, by a dictionary or by a detector, the shorter
forms of that name are protected and linked to it:

- each distinctive word of the name standing alone (`Brightwater` after
  `Brightwater Logistics Ltd`, `Ventisquero` after `Transportes Ventisquero S.L.`, `Ledesma`
  after `Albarrán y Ledesma S.A.`), in lower case too when the organisation writes it
  capitalised (`thanks, brightwater`);
- the name without its legal form (`Brightwater Logistics`, `Transportes Ventisquero`).

Each becomes a variant of the organisation (`[ORG_1_B]`), even when no detector found it. A
shorter form that a detector did find is linked in the same way, instead of getting a number of
its own. Generic words (`Logistics`, `Grupo`), legal forms (`Ltd`, `S.L.`), connectors (`and`,
`y`) and common words (`Stone`) never stand for an organisation on their own, so
`Logistics costs rose` stays as written.

Today these forms reach the provider unchanged unless a detector finds them, and the leak audit
cannot see them. The organisation corpus of [slice 18](slice-18-org-evaluation.md) measures the
gap, and this slice gates it in CI.

This is the second of two slices for backlog §1, part 3. It extends the linking of
[slice 16](slice-16-person-tokens.md) and [slice 17](slice-17-person-forms.md) to `ORG`.

## Depends on

Slice 18 (the organisation corpus and its baseline). Every earlier slice is merged.

## Decisions taken with the maintainer before implementation (2026-11-23)

1. **Two slices, specified together** (slice 18, decision 1).
2. **Which words are shorter forms.** Every word of the name that is not a legal form, a
   connector, a generic word or a common word is a distinctive word, and each distinctive word
   is a shorter form of one word. Legal forms and generic words come from two fixed lists in the
   code (`LEGAL_FORMS`, 34 keys, and `GENERIC_WORDS`, 210 keys, below); common words are
   `COMMON_WORDS` of slice 17. A generic word missing from the list is treated as distinctive:
   it over-replaces and never leaks. Rejected: no generic list, which replaces `Logistics`,
   `Grupo` or `Transportes` wherever they start upper-case, and leaves two organisations that
   share such a word without their links; and only the first distinctive word, which forwards
   `Ledesma` in `Albarrán y Ledesma S.A.`.
3. **The name without its legal form is a shorter form of its own.** `Brightwater Logistics`
   after `Brightwater Logistics Ltd` is searched like a distinctive word and replaced whole.
   Rejected: distinctive words only, which leaves `[ORG_1_B] Logistics`.
4. **Persons first.** A word that is a one-word form of a person and of an organisation follows
   the person rules of slices 16 and 17: `Vidal` after `Lucía Vidal` and `Vidal Logistics`
   stays the person's variant. Organisation forms count only for words that no person has.
   Rejected: a number of its own when both types have the word, which changes the person
   corpus's `org` category, whose shared word names the person.

## Decisions made while writing this spec

- **The engine keyword defaults to off, the configuration to on**, as for `person_tokens`:
  `Pseudonymizer(..., org_tokens=False)` keeps every library caller's output, and
  `detection.org_tokens: true` turns it on for the gateway, `check`, `demo` and the evaluation
  harness.
- **Lower-case forms follow slice 17.** A distinctive word that the first organisation having
  it writes with an upper-case or title-case first letter is lower-case eligible. Common words
  are never distinctive, so slice 17's common-word check is implied. The name without its legal
  form is never lower-case eligible.
- **Common words are not forms, even capitalised** (decision 2). For persons, slice 16 replaces
  a capitalised common word. Organisation names contain closed-class words more often than
  person names do (`All Freight Ltd`, `Más Servicios S.L.`), and replacing those where they
  start upper-case would replace sentence starts. The cost, `Stone` after `Stone Logistics Ltd`, is measured by
  slice 18's `common` category and documented.
- **Detector types never change.** A `PERSON` span links only to persons and an `ORG` span
  only to organisations. `Lewis`, which the ML model labels `PERSON` after
  `Lewis Engineering Ltd`, stays a person of its own: replaced, not linked. Rejected: re-typing
  it, which overrides a detector on a guess.
- **Legal forms are compared without dots.** `S.L.`, `S.L` and `SL` are one legal form, and so
  are `Inc.` and `Inc`.
- **The lists are fixed in the code**, like `COMMON_WORDS`, and written out in full in this
  file. A change to them is a code change with a test, so that its effect on the corpora is
  measured.

## Found while specifying (prototype of 2026-11-23, outside the repository, synthetic data)

The prototype extended the engine's linker with the rules of this file and ran the evaluation
harness on a corpus built like slice 18's (the nine categories, 20 examples each per language),
with a dictionary of its full names and with the ML layer (same model and settings as
`docs/evaluation.md`).

Leak rate per category, before → after (the prototype corpus used one template per category,
so slice 18's corpus gives other numbers):

| Category | en, dictionary | es, dictionary | en, ML | es, ML |
|---|---|---|---|---|
| `full` | 0 → 0 | 0 → 0 | 0 → 0 | 0 → 0 |
| `bare` | 0.4371 → 0 | 0.4228 → 0 | 0.3582 → 0 | 0.4228 → 0 |
| `word` | 0.3754 → 0 | 0.3095 → 0 | 0.0754 → 0.0415 | 0.2278 → 0.1963 |
| `before` | 0.2176 → 0 | 0.2069 → 0 | 0.2765 → 0.2255 | 0.0181 → 0 |
| `multi` | 0.2995 → 0 | 0.3602 → 0 | 0.2622 → 0.1783 | 0.2752 → 0.0994 |
| `shared` | 0.2400 → 0 | 0.1995 → 0 | 0.2293 → 0 | 0.1873 → 0 |
| `generic` | 0.2418 → 0 | 0.2138 → 0 | 0.2637 → 0.0531 | 0.2104 → 0.0370 |
| `lower` | 0.2331 → 0 | 0.2171 → 0 | 0.2139 → 0.0848 | 0.1743 → 0.0158 |
| `common` | 0.1943 → 0.1943 | 0.1694 → 0.1694 | 0.1725 → 0.1725 | 0.1876 → 0.1876 |
| all | 0.2486 → 0.0146 | 0.2303 → 0.0138 | 0.2039 → 0.0733 | 0.1908 → 0.0507 |

- **Links.** With the dictionary, 260 of 260 same-organisation pairs were linked in each
  language (20 before, all in `full`). With the ML layer, 177 of 259 in English (72 of 144
  before) and 175 of 256 in Spanish (50 of 127). No wrong pair in any run. `ORG`
  `precision_exact` stayed 1.0 with the dictionary and rose from 0.940 to 0.960 in English and
  from 0.917 to 0.946 in Spanish with the ML layer. No round-trip failure.
- **Without the bare name** (decision 3), `bare` still leaked 0.2545 in English and 0.2790 in
  Spanish with the dictionary: the generic word next to `[ORG_1_B]`.
- **The prototype kept one pool of candidates for both types**, where this file keeps the
  person candidates exactly those of slice 17. On these corpora both give the same output: the
  organisation corpus names no person, and the person corpus gave identical numbers.

- **No change elsewhere.** The person corpus gave identical numbers, with the dictionary of
  `persons.yaml` and with the ML layer, and the synthetic corpora gave identical output with
  the ML layer (none of their 405 `ORG` spans has a shorter form in its example). Rules alone
  detect no `ORG`, so the CI recall gate cannot change.
- **What the ML layer still leaves.** In Spanish the model often leaves the leading generic word
  out of the span (`Suministros [ORG_1]`), so the generic word is forwarded and the gold span
  counts as leaked in part. It also labels a bare distinctive word `PERSON` (`Lewis`, `Zurita`),
  which is replaced as a person of its own: no leak, a missed link.

## Decisions made while implementing

No question was asked: the slice file left nothing open that could change what reaches the
provider. Where it was silent:

- **With `person_tokens` off**, no person forms are recorded, so "persons first" never drops an
  organisation candidate: only the organisation forms apply, as the file says. The word then
  goes to the organisation, which replaces it; nothing leaks.
- **Persons first is checked twice**, as written: a candidate is not created for a key that is
  already a one-word form of a person, and a pending one is dropped when it is assigned, if a
  person has the key by then (a person named after the organisation in the same or a later
  text). Person candidates are those of slice 17, in their own pool; on a tie of text, start and
  key, the person candidate comes first, which changes nothing since the organisation one is
  then dropped.
- **The linker** (`engine._PersonLinker`, private) is now `engine._NameLinker` and handles both
  types; the person code paths are unchanged. `orgs.OrgWord` has one more read-only property,
  `content`, true for distinctive, generic and common words, which the engine uses.
- **An allow-listed bare name** is not a form, but its distinctive words still are: with
  `Brightwater Logistics` allowed, `Brightwater Logistics` alone after the full name gives
  `[ORG_1_B] Logistics`. The test of the allow list uses `Stone Logistics Ltd`, whose bare name
  has no distinctive word, so that the allow-listed bare name stays whole.
- **Tests beyond the list.** A name that is its own bare name (`Grupo Peñalver`), a part of a
  hyphenated name (`Yang` of `Yang-Clark Ltd`) left as written, the organisation detected before
  the person in one text, a person named in lower case after the organisation (persons first
  at assignment), an allow-listed word removed from a detected run, `analyze` reporting
  undetected forms as propagation, the type check of 2a with `person_tokens` off, a word of two
  organisations found by a detector, and the proxy route of `Brightwater` with a dictionary term.
  `test_orgs.py` also checks the written form of each word, the bare name's inner whitespace and
  punctuation, an empty word, `Co.` and `GmbH`, and that every key of `COMMON_WORDS` outside the
  other lists is `COMMON`.
- **Property generator.** The four properties of slices 16 and 17 now draw, next to the persons,
  eleven organisations of both language shapes (two sharing a distinctive word, two sharing a
  generic word, one whose word is a common word, two sharing a word with a person), their
  distinctive words, bare names, generic words and legal forms alone, the same in random case,
  and five pairs drawn together (`Alice Johnson at Johnson Freight PLC`). A fifth property checks
  that no distinctive word of an organisation is left on its own. A probe of 2,000 batches found
  an organisation in 76% of them, a distinctive-word variant in 14.6%, a bare-name variant in
  4.2%, a lower-case variant in 2.4%, a word with a number of its own in 2.7%, persons first (a
  word of both a person and an organisation replaced as the person) in 8.6%, and an organisation
  word that stays `ORG` when a person with that word comes later in 1.0%. Without the pairs,
  persons first was reached in none of 2,000 batches.
  Each of the five properties also ran once with 20,000 examples and passed.
- **Expectations that changed** with the configuration default (on): only the defaults of
  `tests/test_config.py`, which now include `org_tokens`. Every other test of `main` passes
  unchanged against this code (tests that read files outside `tests/` were run in place). The
  logging invariant test sends `ACME pays.` too, a shorter form of its dictionary's organisation.
- **Mutation checks.** Each condition of the checklist was broken on purpose and a test failed:
  each word list (legal forms, connectors, generic words, common words), the dot removal, the
  minimum length, runs of at least two words, the bare name's two content words, its outer
  punctuation and its allow list, lower-case eligibility from the written form, bare names never
  eligible, persons first, the type check of 2a for both types, the exactly-one-family rule in
  2a and 2b, the allow list of content words, distinctive words only as one-word forms, the
  three switches (`org_tokens` and `person_tokens` off, the engine flag) and the configuration
  default, factory and `check` line (28 mutants). Persons first survived at first: the person
  candidate wins every tie, and assigning a known key returns its entity, so only a person whose
  word is not lower-case eligible (a name detected in lower case) after the organisation in the
  walk shows the rule; a test now covers it. Two mutants are equivalent: dropping the bare
  name's "key different from the organisation's" (that key always has an entity, the
  organisation itself) and dropping "the key has no entity" when a candidate is created (step 2b
  deletes such candidates before searching). The slice file states both conditions, so the code
  keeps them.

**Measured** (2026-11-23, same model and library versions as slice 18). With
`detection.org_tokens: false`, both organisation runs give exactly slice 18's numbers. With it on,
the synthetic corpora (rules only, and ML with `--types PERSON,ORG,ADDRESS`) and the person corpus
(dictionary and ML) give byte-identical JSON output, misses and wrong merges included.

| Run | en leak, slice 18 → 19 | es leak, slice 18 → 19 | en linked / same | es linked / same | wrong |
|---|---|---|---|---|---|
| dictionary | 0.2190 → 0.0127 | 0.2053 → 0.0120 | 280 / 280 | 280 / 280 | 0 |
| ML | 0.1809 → 0.0675 | 0.1651 → 0.0464 | 203 / 286 | 199 / 277 | 0 |

Leak rate per category, slice 18 → slice 19:

| Category | en, dictionary | es, dictionary | en, ML | es, ML |
|---|---|---|---|---|
| `full` | 0 → 0 | 0 → 0 | 0 → 0 | 0 → 0 |
| `bare` | 0.4342 → 0 | 0.4164 → 0 | 0.3444 → 0 | 0.2222 → 0 |
| `word` | 0.3694 → 0 | 0.3458 → 0 | 0.1608 → 0.0239 | 0.2882 → 0.1327 |
| `before` | 0.2449 → 0 | 0.2167 → 0 | 0.3253 → 0.2791 | 0.1109 → 0 |
| `multi` | 0.3024 → 0 | 0.3651 → 0 | 0.2966 → 0.1854 | 0.2531 → 0.0858 |
| `shared` | 0.2402 → 0 | 0.2192 → 0 | 0.1732 → 0 | 0.2092 → 0 |
| `generic` | 0.2335 → 0 | 0.2054 → 0 | 0.2510 → 0.0875 | 0.3328 → 0.1885 |
| `lower` | 0.2334 → 0 | 0.2163 → 0 | 0.1730 → 0.0282 | 0.1730 → 0 |
| `common` | 0.1912 → 0.1912 | 0.1691 → 0.1691 | 0.1451 → 0.1451 | 0.1618 → 0.1618 |
| all | 0.2190 → 0.0127 | 0.2053 → 0.0120 | 0.1809 → 0.0675 | 0.1651 → 0.0464 |

The per-category counts (placed, leaked characters, pairs) are in `docs/evaluation.md`.

## Dependencies to add

None.

## Files

| File | Action |
|---|---|
| `src/aliasgate/orgs.py` | new: the words of an organisation's name, legal forms, generic words |
| `src/aliasgate/names.py` | change: the end-punctuation helper becomes public, unchanged |
| `src/aliasgate/engine.py` | change: organisation forms in steps 2a and 2b |
| `src/aliasgate/config.py`, `src/aliasgate/factory.py` | change: `detection.org_tokens` |
| `src/aliasgate/cli.py` | change: one line in `check` |
| `tests/test_orgs.py` | new |
| `tests/test_engine.py`, `tests/test_config.py`, `tests/test_factory.py`, `tests/test_cli.py`, `tests/proxy/test_app.py`, `tests/proxy/test_invariants.py` | change |
| `tests/eval/test_org_gate.py` | new: the CI gate on the organisation corpus |
| `docs/architecture.md` | change: §2, §4.1, §4.3, §10 |
| `docs/usage.md` | change: §3 (`check` output), §6.3, §7 |
| `docs/threat-model.md` | change: risks 1, 6 and 7 |
| `docs/evaluation.md` | change: the organisation corpus numbers with this slice, the gate |
| `examples/aliasgate.example.yaml` | change: `org_tokens: true` |
| `CHANGELOG.md` | change: `Unreleased` → `Fixed` and `Changed` |

## Specification

Read architecture §4.2 and §4.3 and the "Specification" sections of slices 16 and 17 first.
This slice changes contracts defined there, so the architecture is updated in the same pull
request (AGENTS.md rule 6).

### Vocabulary (architecture §2)

| Term | Meaning |
|---|---|
| **Organisation** | An `ORG` entity, whichever detector or dictionary found it. |
| **Legal form** | A word of an `ORG` surface whose key, with every `.` removed, is in `LEGAL_FORMS` (`ltd`, `sl`, `inc`). |
| **Generic word** | A key in `GENERIC_WORDS`: a word that says what an organisation does or is (`logistics`, `grupo`). |
| **Distinctive word** | A word of an `ORG` surface that is not a legal form, a connector, a generic word or a common word, and is at least `min_propagation_length` long. |
| **Bare name** | An organisation's surface from its first to its last content word, as written: its name without the legal form. |

### Organisation words (`orgs.py`, new, core)

```python
LEGAL_FORMS: Final[frozenset[str]]    # keys, every "." removed
GENERIC_WORDS: Final[frozenset[str]]  # keys
CONNECTORS: Final[frozenset[str]]     # names.PARTICLES and "of", "the", "for", "und", "et"

class OrgWordKind(Enum):
    DISTINCTIVE = auto()
    GENERIC = auto()
    COMMON = auto()
    LEGAL = auto()
    OTHER = auto()  # empty, a connector, or shorter than the minimum

@dataclass(frozen=True, slots=True)
class OrgWord:
    key: str
    word: str  # as written, without the punctuation at its ends
    kind: OrgWordKind

def org_words(surface: str, *, min_length: int) -> tuple[OrgWord, ...]:
    """Every word of an ORG surface, in order, with its key, the word as written without the
    punctuation at its ends, and its kind."""

def bare_name(surface: str, *, min_length: int) -> str | None:
    """The surface from its first to its last content word, as written, without the punctuation
    at the outer ends of those two words, when it has at least two content words; else None."""
```

1. Split the surface on whitespace.
2. Strip from both ends of each word every character whose Unicode category starts with `P`, as
   `names.py` does. Share that helper: `names._unpunctuated` becomes the public
   `names.unpunctuated`, unchanged.
3. Take `normalize_key` of the word.
4. The kind is decided by the first rule that applies: an empty key is `OTHER`; a key that,
   with every `.` removed, is in `LEGAL_FORMS` is `LEGAL`; a key in `CONNECTORS` is `OTHER`; a key
   shorter than `min_length` is `OTHER`; a key in `GENERIC_WORDS` is `GENERIC`; a key in
   `COMMON_WORDS` is `COMMON`; any other key is `DISTINCTIVE`.

The **content words** are the words of kind `DISTINCTIVE`, `GENERIC` or `COMMON`.

Examples, with the default minimum of 3:

- `Brightwater Logistics Ltd` → `brightwater` distinctive, `logistics` generic, `ltd` legal.
  Bare name `Brightwater Logistics`.
- `Transportes Ventisquero S.L.` → generic, distinctive, legal (`S.L` without its dots is
  `sl`). Bare name `Transportes Ventisquero`.
- `Construcciones Albarrán y Ledesma S.A.` → generic, distinctive, other, distinctive, legal.
  Bare name `Construcciones Albarrán y Ledesma`.
- `Rhodes, Young and Charles` → distinctive, common, other, distinctive. Bare name
  `Rhodes, Young and Charles`, its own surface, so the engine does not use it as a form.
- `The Brightwater Company` → other, distinctive, legal. No bare name (one content word).
- `Inversiones EY S.Coop.` → generic, other (two letters), legal. No bare name.
- `ACME Corporation` and `Globex S.L.` → distinctive, legal. No bare name.
- `(Brightwater Logistics) Ltd` → bare name `Brightwater Logistics`.

The lists, as keys (`normalize_key`: lower case, accents kept):

- **`LEGAL_FORMS`** (34 keys): ag bv cia cía co corp corporation company gmbh inc incorporated
  limited llc llp lp ltd ltda nv plc pty sa sad sarl sas sat sau scom scoop scp sl sll slne
  slu srl
- **`GENERIC_WORDS`, English** (115 words): accounting advisors advisory agency airlines
  airways analytics apparel architects associates automotive aviation bakery bancorp bank
  bankers brands brewery brothers builders capital chemicals clinic communications construction
  consultants consulting design developers development digital distribution electric
  electronics energy engineering enterprises entertainment equipment estates farms finance
  financial fitness foods foundation freight furniture games global group health healthcare
  holdings homes hospitality hotel hotels industries institute insurance international
  investments laboratories labs legal lighting logistics management manufacturing marine
  marketing media medical metals mining motors networks partners pharma pharmaceuticals
  plastics printing productions properties publishing realty records research resources
  restaurants retail robotics security services shipping software solutions sons studio studios
  supplies supply systems technologies technology telecom textiles trading transport travel
  trust university ventures wholesale
- **`GENERIC_WORDS`, Spanish** (97 words): abogados academia agencia alimentación alimentos
  arquitectos asesores asesoría asociación asociados automoción avanzadas banca banco bodegas
  castellana centro clínica colegio comercial comercializadora compañía construcción
  construcciones consultores consultoría cooperativa corporación desarrollo desarrollos despacho
  distribuciones distribuidora ediciones editorial electricidad energía energías española
  españolas español españoles estudio fábrica familia farmacéutica farmaceútica finanzas
  fundación gestión gestoría global globales grupo hermanos hnos hotel hoteles iberia ibérica
  ibéricos industrias informática infraestructuras ingeniería inmobiliaria instalaciones
  integrales inteligentes internacional internacionales inversiones laboratorios levante
  logística manufacturas mediterráneo minería noroeste norte privada producciones promociones
  restauración seguros servicios sistemas sociedad soluciones suministros supermercados sur
  talleres tecnologías telecomunicaciones transportes viajes

`global` and `hotel` are in both groups, so `GENERIC_WORDS` has 210 keys. The Spanish group
holds every word Faker's `es_ES` company provider uses (`farmaceútica` is its spelling), so ML
spans of the synthetic corpora keep only their surnames as distinctive words. No key is in
`COMMON_WORDS` or `LEGAL_FORMS`. The module holds the lists as module-level strings and builds
the sets once; it is pure data and functions, does no I/O, and only the engine uses it.

### Engine (`engine.py`, architecture §4.1 and §4.3)

```python
class Pseudonymizer:
    def __init__(self, detectors: Sequence[Detector], *, allow_list: Iterable[str] = (),
                 propagate: bool = True, min_propagation_length: int = 3,
                 person_tokens: bool = False, org_tokens: bool = False) -> None: ...
```

With `org_tokens` off, nothing below applies, and every output is that of slice 17. With
`person_tokens` off and `org_tokens` on, only the organisation forms apply. Words are always
computed with `min_length=min_propagation_length`, and a word whose key is on the allow list is
no content word.

**Forms of an organisation.**

- every contiguous run of at least two of its content words (used in 2a);
- each of its distinctive words, as a form of one word (2a and 2b);
- its bare name, when `bare_name` gives one whose key differs from the organisation's key and is
  not on the allow list (2b).

A single generic or common word is never a form. So `Brightwater Logistics Ltd` has the forms
`(brightwater, logistics)`, `(brightwater)` and the bare name `Brightwater Logistics`;
`Construcciones Albarrán y Ledesma S.A.` has the runs of `(construcciones, albarrán, ledesma)`
of two and three words, `(albarrán)`, `(ledesma)` and its bare name; `Grupo Peñalver` has
`(grupo, peñalver)` and `(peñalver)`, and no bare name, since that is its own surface.

**2a. Linking a detected span.** As for persons, and the span's type decides which entities it
can link to. An `ORG` span is assigned as a variant (`variant_of` the first entity of the
family) when it has no link, its key has no entity yet, its content words are not empty, and
they are one of the forms of at least one organisation that exists at that point of the walk,
all such organisations belonging to one family. A `PERSON` span links only to persons, as in
slice 17. Equal sequences count, so `Brightwater Logistics Limited` links to
`Brightwater Logistics Ltd`. A detected `Brightwater` or `Brightwater Logistics` links; a
detected `Logistics` gets a number of its own; `Pacific Logistics` does not link to
`Pacific Marine Ltd`. A dictionary span with a link keeps its group.

**2b. Words nobody detected** (only with propagation on).

- **Person candidates** are exactly those of slice 17.
- **Organisation candidates** are the keys of the distinctive words and bare names of every
  organisation that exists, except keys that already have an entity of any type and keys that
  are a one-word form of a person that exists. The written form is the distinctive word as
  written, after stripping, in the first organisation that has it, or the bare name. A
  distinctive word is lower-case eligible when that written form starts with an upper-case or
  title-case character; a bare name never is.
- An occurrence on its own is defined as in slice 17: a match of `build_term_pattern` of the
  written form, with the candidate's key, overlapping no span resolved for that text in step 1,
  and starting with an upper-case or title-case character unless the candidate is lower-case
  eligible.
- All candidates that occur on their own, of both kinds, are assigned in one order: their first
  such occurrence (text index, then start), ties broken by key. Each person candidate is
  assigned as in slice 17. Each organisation candidate is dropped if a person that exists has
  its key as a one-word form (persons first); otherwise it is assigned with type `ORG`, the
  surface of its first occurrence, `capitalised=False` when lower-case eligible and
  `capitalised=True` otherwise, as a variant of the family when the organisations of exactly one
  family have it as a distinctive word or bare name, and with a number of its own otherwise.
- Each candidate searches each text at most once, as in slice 16.

Consequences, each covered by a test:

- `Brightwater Logistics` alone after `Brightwater Logistics Ltd` creates two entities at the
  same start, the word first (its key sorts first): `[ORG_1_B]` for `Brightwater` and
  `[ORG_1_C]` for `Brightwater Logistics`. Propagation, longest surface first, gives the range
  to `[ORG_1_C]`, so `[ORG_1_B]` may exist without a replaced occurrence (slice 16 allows this).
- Two organisations with one distinctive word in common (`Brightwater Logistics Ltd` and
  `Brightwater Analytics LLC`) give that word a number of its own.
- Numbering still depends only on the content and order of the texts, and append stability
  holds: step 2b depends only on the step 1 spans and on the texts. A person that a later text
  names never changes an organisation variant assigned earlier.
- Occurrences found in step 3 keep source `"propagation"`; `BatchStats`, the metrics, the leak
  audit and `analyze` need no change.

### Configuration, factory and CLI

- `DetectionSettings.org_tokens: bool = True`. The environment variable is
  `ALIASGATE_DETECTION__ORG_TOKENS`.
- `build_pseudonymizer` passes it to the engine.
- `aliasgate check` prints `org tokens: on` or `off` after the `person tokens:` line. Update
  the sample output in usage §3.
- `examples/aliasgate.example.yaml`: `org_tokens: true` after `person_tokens`, with a short
  comment.

### Gate (`tests/eval/test_org_gate.py`)

It runs with `tests/eval/corpus/orgs.yaml` and the default configuration otherwise, so
`person_tokens` and `org_tokens` are on, and groups examples by the category in their ids, as
the person gate does. For each language:

- In `full`, `bare`, `word`, `before`, `multi`, `shared`, `generic` and `lower`, every letter
  and digit of every gold `ORG` span is covered by a predicted span, `wrong_pairs` is 0 and
  `link_recall` is 1.0.
- In `generic`, no predicted span lies outside the gold spans: the generic word on its own is
  not replaced.
- Over the whole corpus, there are no round-trip failures.

`common` is measured and documented, not asserted: its distinctive word is a common word, which
decision 2 leaves as written. The person gate (`tests/eval/test_person_gate.py`) runs unchanged
and must still pass. If a gate cannot be met, stop and report it. Change neither the corpus nor
a threshold to make it pass (AGENTS.md rule 9).

### Evaluation

- Re-run both baseline commands of slice 18 and record the new numbers next to the old ones in
  `docs/evaluation.md`, per category too.
- The person corpus and the synthetic corpora must give the same numbers as before, with the
  dictionary or rules only and with the ML layer. If they do not, stop and report it.
- `expected_restoration` needs no change. If a round-trip failure appears, stop and report it.

### Documentation

- **Architecture.**
  - §2: the five terms above.
  - §4.1: the `org_tokens` keyword.
  - §4.3: organisations, their forms, 2a for `ORG` spans, organisation candidates in 2b, and
    persons first.
  - §10: a decision row. Decision: the distinctive words of an organisation's name and the name
    without its legal form become variants; legal forms, connectors, generic words and common
    words never stand for it alone. Rejected alternative: every word, the first distinctive
    word only, or common words where capitalised. Reason: every word replaces `Logistics`
    everywhere; the first word only forwards `Ledesma`; capitalised common words replace
    sentence starts. A gap in the generic list over-replaces and never leaks.
- **Usage.**
  - §3: the `check` line.
  - §6.3: `org_tokens`: what it does, that it needs a detected full name (a dictionary term or
    an ML span), that it needs propagation for undetected shorter forms, which words count, and
    how to turn it off.
  - §7: these bullets:
    - shorter forms of an organisation are linked (`[ORG_1_B]`) and restored as written;
    - a capitalised distinctive word elsewhere is replaced too, and a generic word missing from
      the list counts as distinctive;
    - a distinctive word that is also a common word (`Stone` for `Stone Logistics Ltd`) is left
      as written;
    - a word shared by a person and an organisation goes to the person (keep the bullet about
      `Vidal Logistics`, reworded).
- **Threat model.**
  - Risk 1: what is still sent as written: a distinctive word that is a common word, acronyms
    (`GHS`), parts of a hyphenated organisation name, words shorter than
    `min_propagation_length`, and the words of an organisation that a detector returns in lower
    case, in lower case.
  - Risk 6: wrong links: in a later message, another organisation that shares a distinctive
    word with an earlier one gets the earlier one's variant; a word shared with a person goes
    to the person. A utility loss, not a leak.
  - Risk 7: the provider sees which placeholders are forms of one organisation.
- **CHANGELOG** (`Unreleased`):
  - `Fixed`: the shorter forms of a detected organisation's name (a distinctive word, the name
    without its legal form) were forwarded unchanged.
  - `Changed`: a shorter form that a detector found is now a variant of the full name, which
    changes the numbering, and `detection.org_tokens` turns this off.

## Tests

Synthetic values only, from the shared test data, slice 18's examples and these lists. Engine
tests use `WordListDetector` from `tests/fakes.py` with the full names as `ORG` words and pass
`org_tokens=True` (and `person_tokens=True` where persons appear) unless the test says
otherwise.

`test_orgs.py`
- The examples of the "Organisation words" section, each as a test, for `org_words` and
  `bare_name`.
- Legal forms with and without dots and in any case: `S.L.`, `S.L`, `SL`, `s.l.`, `LTD`,
  `Inc.`, `S.Coop.`, `S.L.N.E`, `Cía.`.
- Every key of `LEGAL_FORMS`, `GENERIC_WORDS` and `CONNECTORS` gets its kind. A word that merely
  contains one (`Grupon`, `Incas`, `Solutionsx`) is distinctive.
- `min_length` turns shorter words into `OTHER`; `EY` is `OTHER` with the default of 3.
- Keys are NFKC and case-folded (`ＢＲＩＧＨＴＷＡＴＥＲ` → `brightwater`).
- `GENERIC_WORDS` holds every generic word of `tools/generate_org_corpus.py`; every key is
  `normalize_key` of itself, has no whitespace, and is not in `COMMON_WORDS`, `LEGAL_FORMS` or
  `CONNECTORS`. `LEGAL_FORMS` keys contain no `.`. The counts are 34 and 210.

`test_engine.py`
- `["We signed with Brightwater Logistics Ltd, as agreed. Brightwater will send the invoice."]`
  gives `We signed with [ORG_1], as agreed. [ORG_1_B] will send the invoice.`, and restoring it
  gives back the input.
- Spanish: `Transportes Ventisquero S.L. firmó. Ventisquero enviará la factura.` gives
  `[ORG_1] firmó. [ORG_1_B] enviará la factura.`
- The bare name: `Brightwater Logistics Ltd signed. Brightwater Logistics will pay.` gives
  `[ORG_1] signed. [ORG_1_C] will pay.`, the mapping holds `[ORG_1_B]` for `Brightwater`, and
  restoring gives back the input.
- A word that is not the first: `Albarrán y Ledesma S.A. reclama. Ledesma quiere la factura.`
  gives `[ORG_1_B]` for `Ledesma`.
- A generic word stays: `Andrews Consulting Inc. sent it. Consulting costs rose.` leaves
  `Consulting`, and the mapping has no entity for it. The same for a legal form (`Ltd`), a
  connector (`the`) and a common word (`Stone` after `Stone Logistics Ltd`), each standing
  alone.
- Two organisations with the same generic word: `Welch` and `Pitts` link to their own
  organisations, and `Logistics` alone is not replaced.
- Two organisations with the same distinctive word: `Brightwater` alone gets `[ORG_3]`.
- Lower case: `Brightwater Logistics Ltd confirmed. thanks, brightwater will call.` gives
  `thanks, [ORG_1_B] will call.`, restored as written. A detector that returns
  `brightwater logistics ltd` in lower case gives no lower-case replacement of `brightwater`,
  and `Brightwater` elsewhere is replaced.
- The bare name is not replaced in lower case: `brightwater logistics` after the full name
  gives `[ORG_1_B] logistics`.
- Persons first: `Lucía Vidal works at Vidal Logistics. Vidal will send the quote.` with both
  names detected gives `Vidal` as `[PERSON_1_B]`, exactly as with `org_tokens` off. Without the
  person, `Vidal Logistics Ltd signed. Vidal will call.` gives `[ORG_1_B]`. A word on
  `COMMON_WORDS` (`Banks` of `Banks Logistics`) is no organisation form in any case.
- Persons first, across texts: an organisation word assigned in text 0 stays `ORG` when text 1
  names a person who has that word.
- 2a: a detected `Brightwater` (`ORG`) after the full name is `[ORG_1_B]`, a detected
  `Brightwater Logistics` is a variant, a detected `Brightwater Logistics Limited` is a
  variant, a detected `Logistics` is `[ORG_2]`, a detected `Pacific Logistics` after
  `Pacific Marine Ltd` is `[ORG_2]`, and a detected `PERSON` span `Brightwater` is `[PERSON_1]`.
- A shorter form in text 0 and the full name in text 1: in the two-text batch, the form in text
  0 is the variant; in a batch of text 0 alone, it is not replaced.
- `propagate=False`: no entity for an undetected word, and a detected `Brightwater` still links.
- The allow list: an allow-listed distinctive word is not replaced, and an allow-listed bare
  name is not either.
- A dictionary `ORG` term with an alias: its words take letters after the aliases seen so far.
- `org_tokens=False`: the first input of this list gives
  `We signed with [ORG_1], as agreed. Brightwater will send the invoice.`, and a detected
  `Brightwater` is `[ORG_2]`. `person_tokens=False` with `org_tokens=True` links organisation
  words and no person words.
- Extend the four properties of slices 16 and 17 (round trip, no residue, append stability,
  determinism) with organisations in the generator: full names in both language shapes, their
  distinctive words, bare names and generic words alone, in random case, next to persons, at
  least 200 examples each. No residue now also covers organisations: no distinctive word of at
  least 3 characters that starts upper-case, and no lower-case eligible one, is left on its own
  in the output. Check how often the generator reaches each case, and record it in the pull
  request.
- Search bound: 1,000 texts, each with a new organisation whose words never stand alone, run in
  under 15 s, as slice 16's test does for persons.

`test_config.py`, `test_factory.py`, `test_cli.py`
- `org_tokens` defaults to True and can be set from the file and from the environment.
- The factory passes it on: with it on and off, a request with
  `Brightwater Logistics Ltd … Brightwater` gives the two outputs.
- `check` prints the new line.

`proxy/test_app.py`
- With a dictionary term `Brightwater Logistics Ltd` (type `ORG`, no ML), an OpenAI request with
  `Brightwater Logistics Ltd wrote. Brightwater will call.` reaches the mocked upstream as
  `[ORG_1] wrote. [ORG_1_B] will call.`. The response `"[ORG_1_B] and [ORG_1]"` reaches the
  client as `Brightwater and Brightwater Logistics Ltd`, and `x-aliasgate-entities` is `2`.
- The logging invariant test (`tests/proxy/test_invariants.py`) still passes with a request
  that contains organisation shorter forms.

`tests/eval/test_org_gate.py`
- The gates of the "Gate" section.

## Acceptance checklist

- [ ] With `org_tokens` off, every pre-existing test passes with unchanged expectations. With
      the configuration default (on), every test whose expectation changed is listed in the pull
      request, with the reason.
- [ ] The organisation gate and the person gate pass. The slice 18 numbers and the new ones are
      both recorded in `docs/evaluation.md`. The person corpus and the synthetic corpora give
      the same numbers as before.
- [ ] Round trip, no residue, append stability and determinism hold with organisations, with at
      least 200 Hypothesis examples each, and each ran once with 20,000 examples. The pull
      request records it.
- [ ] Mutation checks: break each condition on purpose and confirm a test fails. The conditions
      are each word list (legal forms, connectors, generic words, common words), the dot removal
      of legal forms, runs of at least two words, the bare name (at least two content words, a
      key different from the organisation's), lower-case eligibility from the written form,
      bare names never lower-case eligible, persons first, the type check of 2a, the
      exactly-one-family rule, the allow list, and `org_tokens` off. The pull request lists
      them.
- [ ] Architecture, usage, threat model, evaluation, example configuration and CHANGELOG are
      updated in the same pull request.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Acronyms (`GHS` for `Global Harbour Shipping`), the parts of a hyphenated organisation name
  (`Yang` of `Yang-Clark`), brand nicknames and transliterations.
- Common words as organisation forms, generic words of other languages, and a setting to extend
  the lists.
- Changing a detector's type, or linking across types.
- Other types (`ADDRESS`, `PROJECT`).
- Replacing a run of words as one span when nobody detected it, other than the bare name.
- Changes to detectors, the resolver, placeholders, restoration, streaming or the leak audit.

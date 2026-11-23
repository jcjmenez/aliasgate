# Slice 17: lower-case, one-word, hyphenated and `Don` person forms

## Goal

The shorter forms of a person's name that slice 16 still forwards are protected and linked to
the person:

- a shorter form in lower case (`thanks larry`, `gracias priscila`), unless the word is a
  common English or Spanish word (`the brown box`, `la mesa`);
- the surname of a detected name that has only one name word, after a title or an initial
  (`Holloway` after `Mr. J. R. Holloway`, `Pérez` after `Sr. Pérez`);
- one part of a hyphenated name (`Anne` for `Anne-Marie Fowler`, `Lorca` for
  `Federico García-Lorca`);
- the English first name `Don` (`Don` and `Draper` after `Don Draper`), while `Don't` and the
  Spanish title (`Don Juan Pérez`) stay as written.

Today all of these reach the provider unchanged unless a detector finds them, and a detector
that does find one gives it a number of its own. The person corpus gains three categories, and
the CI gate grows to cover them and the lower-case forms.

This slice continues backlog §1 after [slice 16](slice-16-person-tokens.md). It changes the
engine's name tokens and its step 2b, and the whole-term pattern shared by propagation, the
dictionary and the leak audit.

## Depends on

Slice 16 (person name tokens and linking), released in v0.4.0. Every earlier slice is merged.

## Decisions taken with the maintainer before implementation (2026-11-22)

1. **Lower-case forms are replaced unless the word is a common word.** A name word that stands
   alone in lower case is replaced as the person's variant, unless its key is on a hand-written
   list of English and Spanish common words (`COMMON_WORDS`, below). The list holds closed-class
   words (`and`, `para`, `con`) and common words that are also names (`brown`, `mesa`, `rubio`,
   `rosa`). A word on the list keeps slice 16's rule: replaced only where it starts with an
   upper-case letter. A word missing from the list is replaced in lower case too, so a gap in
   the list over-replaces and never leaks. Rejected: a list of closed-class words only, which
   replaces `el repartidor rubio` next to `Ana Rubio` and changes the `word` gate; no list,
   which also replaced the `and` of an ML span `Rhodes, Young and Charles` in the synthetic
   corpus; and no change, which keeps the leak.
2. **A detected name with one name word counts as a person** when it also has a title or an
   initial (`Dr. Smith`, `Sr. Pérez`, `J. R. Holloway`). Its name word alone becomes its
   variant.
3. **Each part of a hyphenated name word is a shorter form of its own** (`Anne`, `Marie` and
   `Anne-Marie` for `Anne-Marie Fowler`). Parts do not form runs with the other words.
4. **`Don` is the English first name when the rest of the name has exactly one name word**
   (`Don Draper`). With two or more (`Don Juan Pérez`), it stays the Spanish title. `and` joins
   the particles.
5. **One slice.** The new corpus categories and the engine change ship in one pull request. The
   baseline of the new categories is measured with the slice 16 engine, before the engine
   changes, and recorded next to the new numbers.

Rejected for this slice: nicknames and diminutives (`Bob` for `Robert`, `Pepe` for `José`,
`Juanito`). They need a hand-written table and risk wrong merges. Dictionary aliases (slice 13)
already cover known persons.

## Found while specifying (prototype of 2026-11-22, outside the repository, synthetic data)

The prototype changed the engine's linker and ran the evaluation harness on the committed
corpora, with the dictionary of `persons.yaml` and with the ML layer (same model and settings as
`docs/evaluation.md`).

- **Lower case.** With the common-word list of this file and every rule below, the `lower`
  category leaked 23 of 382 `PERSON` characters in English (was 119) and 4 of 557 in Spanish
  (was 128). What still leaked were the names on the list (`ramos`, `moreno`, `amber`,
  `crystal`, `mar`) and the space inside `juan luis`. The leak rate of the person corpus fell
  from 0.0425 to 0.0303 in English and from 0.0339 to 0.0225 in Spanish with the dictionary,
  and from 0.0201 to 0.0079 and from 0.0168 to 0.0053 with the ML layer. No other category
  changed. Without a list, the leak rate fell further, to 0.0273 and 0.0222, but the lower-case
  common words of `word` (20 per language) were replaced too.
- **The other three gaps, ML on, hand-written synthetic texts.** `Holloway` after
  `Mr. J. R. Holloway`, `José` after `María-José Romero`, `Anne` after `Anne-Marie Fowler`,
  `Lorca` after `Federico García-Lorca`, and `Don` and `Draper` after `Don Draper` were
  forwarded unchanged, and `Smith` after `Dr. Smith` and `Pérez` after `Sr. Pérez` got numbers
  of their own. With the change, each became the person's variant. `Don Pedro` after
  `Don Juan Pérez` was left as it was.
- **No change elsewhere.** The synthetic corpora gave the same numbers with every change on,
  rules only and with the ML layer (with it, leak rate 0.0160 in English and 0.0114 in Spanish,
  and the same predicted spans). No text of the committed corpora contains a word followed by
  `'t`.
- **ML spans that are not persons.** GLiNER labels 37 spans of several words in the English
  synthetic corpus as `PERSON` that are not gold persons, nearly all of them company names
  (`Rhodes, Young and Charles`), some in lower or upper case. In Spanish it labels `El cliente`
  7 times. Without care, `and`, `cliente` or the lower-case words of such a span
  would be replaced everywhere in the request. Hence: `and` is a particle; a one-word name needs
  a title or an initial, and a particle is not enough (`El cliente` is not a person); and only a
  name word written with an upper-case letter in the detected name is replaced in lower case
  (`hall, christian and wells` gives no lower-case replacement).
- **`Don't`.** `build_term_pattern("Don")` matches the `Don` of `Don't`, because `'` is not a
  word character. With `Don` as a name word, a sentence that starts with `Don't` would show a
  placeholder. The same already happens with `Won't` next to a person named `Won`, and with
  `Can't` next to `Can`. Hence the term boundary below.

## Decisions made while writing this spec

- **No new setting.** `detection.person_tokens` turns all of this on or off. A gap in the
  common-word list over-replaces, and every replacement is restored, so there is no leak to
  configure away.
- **The common-word list is fixed in the code**, like `TITLES` and `PARTICLES`, and written
  out in full in this file. A change to it is a code change with a test, so that its effect on
  the corpora is measured.
- **The `'t` boundary applies to every whole term.** A whole term is never followed by `'t` or
  `’t` and the end of a word, wherever whole terms are matched: propagation, the dictionary,
  the leak audit (both searches), and step 2b. A narrower rule, for name words only, would need
  a new entity field, so that propagation and the audit could tell those entities apart. No
  value of any type ends in front of `'t`.
- **The written form decides lower-case eligibility.** A name word is replaced in lower case
  only when the first person that has it writes it with an upper-case or title-case first
  letter. A detector that returns a name in lower case gives no lower-case replacements.
- **The new categories are appended.** The generator writes them after the existing categories
  of both languages, so the 280 existing lines of each file and the existing terms of
  `persons.terms.yaml` stay byte for byte as they are.

## Decisions made while implementing

One question was asked on 2026-11-23, and the maintainer chose the recommended option:

1. **The allow list and `is_person`.** "Persons" says that `is_person` holds "with the allow
   list applied as in slice 16", but `is_person(surface, *, min_length)` takes no allow list.
   Decision: `is_person` keeps that signature and decides on the surface as detected; the
   allow list only removes forms (name tokens and name parts) from those that are replaced and
   linked. So with `jordan` allowed, `Michael` after `Michael Jordan` is replaced, where slice
   16 left it (a detected name with one name token left after the allow list was no person).
   Rejected: counting only the tokens and parts that are not allowed, which needed an extra
   parameter of `is_person` and kept that word as written.

Other choices:

- **The generator keeps new terms where they belong.** The new categories add 120 dictionary
  terms, and a term of a new category that occurred in an earlier text (`Dr. Holloway` in a
  `title` example), or an earlier term inside a new text (`Marie Fowler` inside
  `Anne-Marie Fowler`), would change what the dictionary finds in the earlier examples. For the
  new categories only, the generator draws again in both cases (`_Corpus.isolated`), so the 14
  earlier categories give exactly the slice 16 numbers. The generator's `_Example.full_names` is
  now `terms`, since `single` has no full name; its slots gain `initial_last`, `first_head`,
  `last_tail`, `don` and the `|capital` modifier; and `_Names.persons` receives the template's
  forms as `a.form` labels. The header of `persons.terms.yaml` says what a term is now; the
  entries are pinned, not the header.
- **The `Don` rule, precisely.** "Without a final `.`" means that the punctuation stripped from
  the end of the word holds no `.`. With a `min_length` above 3, `Don` is never a name token,
  so it counts as a title in `is_person`.
- **`da` is a title.** NFKC turns the title `Dª` into `da`, which is also a particle. The
  definition of a title is applied as written, so `da Silva` is a person and `Silva` alone is
  replaced. That over-replaces; it leaks nothing.
- **Expectations that changed with person tokens on.** In `tests/test_engine.py`: the shorter
  forms of `ALICE` are now created with `capitalised=False`, because neither word is a common
  word; `test_a_name_with_a_single_name_token_is_not_a_person` became
  `test_a_name_with_one_name_token_and_a_title_or_an_initial_is_a_person`, and
  `test_a_name_word_that_occurs_only_in_lower_case_creates_no_entity` became
  `test_a_lower_case_shorter_form_is_replaced_and_restored_as_written`, by decisions 2 and 1
  taken before implementation; the "no residue" property now also covers lower-case eligible words and name parts. In
  `tests/eval/`: the counts (17 categories, 340 examples), the dictionary order (the new
  categories are drawn last), and the invariants the spec changes (an initial in `single`, the
  first name `Don`, `Don't` outside the spans, hyphen parts as name words); the gate test of
  `word` now also covers `don`. In `tests/test_audit.py`, the reference implementation of the
  combined search uses the shared boundaries. No pre-existing test matched a term in front of
  `'t`.
- **Tests beyond the list.** `Don Juan`, `don Juan`, `Don. Draper` and the minimum length for
  `Don`; name parts with a dotted title, an empty part and normalised keys; `Don'tcha`,
  `Juan Pérez't` and `won’t` at the boundary; a detected surname after `Dr. Smith` (2a);
  `J. R. Smith`; a name detected in lower case; the allow list on a person and on a part; the
  generator's `Don't` exemption, its hyphen-part check and its isolation check; the content of
  each new category; the pins of the earlier lines and entries, in the committed files and in
  the generator's output; and a detected `José Romero` that does not link to
  `María-José Romero`.
- **The property generator.** The names of this slice and their shorter forms are drawn by two
  samplers of their own, so that most batches meet them. Of 2,000 sampled batches, 38% replaced
  a lower-case occurrence, 35% created an entity first seen in lower case, 31% a variant from a
  hyphen part, 14% the surname variant of `Dr. Holloway`, 11% held `Don't` next to
  `Don Draper`, and 8% a `Don` variant. Before the samplers, `Don` and the title name came up
  in 0.9% and 2.8% of the batches.
- **Measured.** The four person properties ran with 300 examples each, and once with 20,000,
  as did the properties of `test_terms.py` and `test_audit.py`. The tests written for main
  pass against this code, except the four engine cases with person tokens on whose expectation
  the spec changes and the generator's category list. Each condition of the acceptance
  checklist was broken on purpose and a test failed: the common-word check, the written-form
  check of eligibility, lower-case occurrences of eligible words, `capitalised=False` for them,
  the `and` particle, each condition of the `Don` rule (no final `.`, capitalised, exactly one
  other name token, the minimum length), the title, the initial and the particle in
  `is_person`, the two-part condition, the title and particle checks of `name_parts`, parts
  kept out of runs (both as words of a run and combined with the other words), parts as forms,
  the allow list on parts, the `'t` boundary of `build_term_pattern` (propagation and 2b), of
  the dictionary, of the audit's term search and of its key search, and the generator's order.
- **Numbers.** `docs/evaluation.md` has every table. With the dictionary, the leak rate of the
  first 280 examples fell from 0.0425 to 0.0303 in English and from 0.0339 to 0.0225 in
  Spanish; with the ML layer, from 0.0201 to 0.0079 and from 0.0168 to 0.0053, as the prototype
  measured. Of the 14 earlier categories only `lower` changed, in both runs. The synthetic
  corpora gave identical output, rules only and with the ML layer.

## Dependencies to add

None.

## Files

| File | Action |
|---|---|
| `src/aliasgate/common_words.py` | new: the common-word list |
| `src/aliasgate/names.py` | change: `and`, the `Don` rule, `name_parts`, `is_person` |
| `src/aliasgate/terms.py` | change: `TERM_START`, `TERM_END`, the `'t` boundary |
| `src/aliasgate/engine.py` | change: persons, parts, lower-case occurrences in step 2b |
| `src/aliasgate/audit.py`, `src/aliasgate/detectors/dictionary.py` | change: use the shared boundary |
| `tools/generate_person_corpus.py` | change: categories `single`, `hyphen`, `don` |
| `tests/eval/corpus/persons.en.jsonl`, `persons.es.jsonl`, `persons.terms.yaml` | regenerated: 60 examples appended per language |
| `tests/test_names.py`, `tests/test_terms.py`, `tests/test_engine.py`, `tests/test_audit.py`, `tests/detectors/test_dictionary.py`, `tests/proxy/test_app.py` | change |
| `tests/test_common_words.py` | new |
| `tests/eval/test_person_corpus.py`, `tests/eval/test_person_gate.py` | change |
| `docs/architecture.md` | change: §2, §4.3, §8, §10 |
| `docs/usage.md` | change: §6.3, §7, §9 |
| `docs/threat-model.md` | change: risks 1 and 6 |
| `docs/evaluation.md` | change: the new categories and the numbers |
| `CHANGELOG.md` | change: `Unreleased` |

## Specification

Read architecture §4.3 and §8 and the "Specification" of slice 16 first. This slice changes
contracts defined there, so the architecture is updated in the same pull request (AGENTS.md
rule 6).

### Vocabulary (architecture §2)

| Term | Meaning |
|---|---|
| **Name token** | A word of a `PERSON` surface that can name the person on its own: not a title, a particle or an initial. `Don` is a name token only in the case given below. |
| **Name part** | A part of a hyphenated name token that is itself long enough and not a title or a particle (`anne` and `marie` of `anne-marie`). |
| **Person** | A `PERSON` entity whose surface has at least two name tokens, or one name token together with a title or an initial, or one name token with at least two name parts. |
| **Common word** | A key in `COMMON_WORDS`: a word whose lower-case use is common in English or Spanish text. |

### Common words (`common_words.py`, new, core)

```python
COMMON_WORDS: Final[frozenset[str]]
"""Keys of English and Spanish words whose lower-case use is common: a name token on this list
is replaced only where it starts with an upper-case letter."""
```

The module holds the words below as module-level strings, one per group, and builds the set
with `normalize_key`. It is pure data, does no I/O, and only the engine uses it. Every key is
written as `normalize_key` gives it: lower case, accents kept.

- **English, closed-class and courtesy words:** a about above across after again against all
  almost along also although always am among an and another any anybody anyone anything are
  around as at away back be because been before behind being below beside besides between
  beyond both but by bye can cannot cheers could dear did do does doing don done down during
  each either else enough even ever every everybody everyone everything few for from further
  get gets got had has have having he hello her here hers herself hey hi him himself his how
  however i if in into is it its itself just least less like many may me might mine more most
  much must my myself near need neither never next no nobody none nor not nothing now of off
  often oh ok okay on once one only onto or other others ought our ours ourselves out over own
  per perhaps please quite rather regards same shall she should since so some somebody someone
  something soon sorry still such than thank thanks that the their theirs them themselves then
  there these they this those though through thus till to together too toward towards under
  unless until up upon us very via was we well were what whatever when where whether which
  while who whom whose why will with within without won would yeah yes yet you your yours
  yourself yourselves
- **English, common words that are also names:** amber april art august banks bell best bill
  black bond booth brown bush case cash chance chase church cook cross crystal daisy dawn day
  dean duke earl east faith field fields fox frank frost gold grace gray green grey guy hall
  heather holly honor hope hunt iris ivy jack jade joy june king knight lake lamb lane lily
  little long major mark miles moon north page park parks pat pearl penny post power price
  prince ray rice rich rob rock rose ruby rush sandy sharp short silver snow south sterling
  stone storm street strong sue summer swan violet wall ward waters wells west white winter
  wise wolf wood woods young
- **Spanish, closed-class and courtesy words:** a adiós ahí ahora al algo alguien algún alguna
  algunas alguno algunos allí ante antes aquel aquella aquellas aquello aquellos aquí así aun
  aún bajo bien buenas bueno buenos cada casi como cómo con contra cual cuál cuales cuando
  cuándo cuanto cuánto de del desde don donde dónde doña durante e el él ella ellas ello ellos
  en entre era eran eres es esa esas ese eso esos esta está estaba estado están estar este esto
  estos fue fueron gracias ha había han hasta hay he hemos hola hoy la las le les lo los luego
  mañana más me mi mí mis misma mismo mucha mucho muy nada ni ningún ninguna ninguno no nos
  nosotras nosotros nuestra nuestras nuestro nuestros o os otra otras otro otros oye para
  perdón pero poco por porque pues que qué quien quién quienes saludos se sea según ser si sí
  siempre sin sino sobre son su sus también tan tanto te ti tiene tienen toda todas todo todos
  tras tu tú tus un una unas uno unos usted ustedes vale vosotras vosotros vuestra vuestro y ya
  yo
- **Spanish, common words that are also names:** águila alba alegre amado amparo ángel ángeles
  asunción aurora barba benigno blanca blanco bravo bueno caballero calvo campo campos cano
  castaño castillo celeste clara clemente concepción conde constante consuelo cordero cortés
  crespo cruz delgado dolores domingo duque esperanza estrella feliz flores franco fuente
  fuentes gallego gallo guerra hermoso herrero iglesia iglesias inmaculada jesús justo leal
  león llano lobo luna luz mar marqués mesa milagros modesto montes mora moreno nieto nieves
  olmo palacio palacios paloma pardo pastor pastora paz peña piedra pilar pino plácido plaza
  pozo prado primo pura puerta ramos remedios rey reyes rico río ríos robles roca rocío rojo
  romero rosa rosario rubio salvador santos serrano severo sierra sobrino socorro sol soledad
  toro torre torres valle vega victoria

Words listed in two groups (`a`, `he`, `me`, `no`, `don`, `bueno`) count once: 633 keys in
all. The Spanish groups follow Spanish alphabetical order. The list must hold every name of the
generator's `WORD_SURNAMES` and `WORD_FIRST_NAMES`. A word is not added because a test fails; a
change to the list is reasoned and recorded in the pull request.

### Name tokens (`names.py`)

```python
def name_tokens(surface: str, *, min_length: int) -> tuple[str, ...]: ...          # unchanged
def name_words(surface: str, *, min_length: int) -> tuple[tuple[str, str], ...]: ... # unchanged
def name_parts(word: str, *, min_length: int) -> tuple[tuple[str, str], ...]:
    """The name parts of a hyphenated name token as pairs of key and part as written, in
    order; empty for a word without a hyphen."""
def is_person(surface: str, *, min_length: int) -> bool:
    """Whether a PERSON surface names a person: at least two name tokens, one name token with
    a title or an initial, or one name token with at least two name parts."""
```

The four steps of slice 16 stay, with these changes:

- `PARTICLES` gains `and`.
- **`Don`.** A word whose key is `don`, without a final `.`, and whose first character is
  upper-case or title-case, is a name token when the other words of the surface give exactly
  one name token. Otherwise it is dropped as a title, as before. So
  `Don Draper` → `("don", "draper")`, `Mr. Don Draper` → `("don", "draper")`, `Don J. Smith` →
  `("don", "smith")`, `Don Juan Pérez` → `("juan", "pérez")`, `don Pedro` → `("pedro",)` and
  `Don.` → `()`. A known miss: the Spanish title before a first name alone, capitalised, reads
  as a name (`Don Juan` → `("don", "juan")`), so a later `Don` before another name may show
  that person's variant. In lower case (`don Juan`) it stays the title.
- **Parts.** `name_parts` splits a word on `-`, U+2010 and U+2011. A part becomes a name part
  when its key is at least `min_length` long, its key with one final `.` removed is not in
  `TITLES`, and its key is not in `PARTICLES`. `María-José` → `(("maría", "María"),
  ("josé", "José"))`, `Smith-Li` → `(("smith", "Smith"),)`, `Jean-de` → `(("jean", "Jean"),)`,
  `Fowler` → `()`.
- **`is_person`.** A title is a word whose key with one final `.` removed is in `TITLES`, other
  than a `Don` that counts as a name token. An initial is a word of one letter, with or without
  a final `.`. A particle alone does not make a person: `El cliente` and `de la Rosa` are not
  persons, and neither is `Smith`. `Dr. Smith`, `Mr Smith`, `J. Smith`, `Sr. Pérez`,
  `Anne-Marie` and `Alice Johnson` are.

`name_tokens` keeps its signature; its result changes only for words equal to `and` or `Don`.

### Term boundary (`terms.py`)

```python
TERM_START: Final[str] = r"(?<!\w)"
TERM_END: Final[str] = r"(?!\w|['’]t(?!\w))"
```

`build_term_pattern` becomes `TERM_START + (?:body) + TERM_END`, compiled as before. The
dictionary's combined pattern and the audit's `_whole_terms` use the same two constants, so that
all four searches agree. Effect: `Don` does not match in `Don't` or `Don’t`, and `Can` does not
match in `can't`, while `Don's`, `Don,` and `Don.` still match. Update architecture §4.3 step 3
and §8.

### Engine (`engine.py`, architecture §4.3)

With `person_tokens` off, nothing below applies, and the output is that of slice 16, apart from
the `'t` boundary, which applies to propagation, the dictionary and the audit in every case.

**Persons.** A person is an entity of type `PERSON` for which `names.is_person` holds, with the
allow list applied as in slice 16. This replaces "at least two name tokens" in 2a and 2b. The
**forms** of a person are:

- every contiguous run of its name tokens (as in slice 16; a hyphenated token is one word of
  the run);
- every name part of each of its name tokens, as a form of one word.

So `Anne-Marie Fowler` has the forms `(anne-marie)`, `(fowler)`, `(anne-marie, fowler)`,
`(anne)` and `(marie)`, and `Dr. Smith` has the form `(smith)`.

**2a. Linking a detected span.** Unchanged, with "a contiguous run of the name tokens of at
least one person" read as "one of the forms of at least one person". A detected `José` after
`María-José Romero` links. A detected `José Romero` does not, because a part is not a word of a
run.

**2b. Words nobody detected.** The candidates are the keys of the one-word forms of every
person that exists (name tokens and name parts), except keys that already have an entity, of
any type. `word` is the token or part as written, after stripping, in the first person that has
it. A candidate is **lower-case eligible** when its key is not in `COMMON_WORDS` and `word`
starts with an upper-case or title-case character. An occurrence on its own is a match of
`build_term_pattern(word)` that has the candidate's key and overlaps no span resolved for that
text in step 1, and that either starts with an upper-case or title-case character or belongs to
a lower-case eligible candidate. A lower-case eligible candidate is assigned with
`capitalised=False`; any other with `capitalised=True`, as before. The order of assignment,
the choice between a variant and a number of its own, and the once-per-text search are those of
slice 16.

Consequences, each covered by a test:

- An entity with `capitalised=False` is propagated and audited like any other entity: every
  case, and in the audit also by its folded key.
- Its surface is that of its first occurrence on its own, so `thanks larry` before `Larry`
  gives an entity that restores as `larry`. This is the rule for every entity.
- Append stability holds as in slice 16: step 2b still depends only on the step 1 spans and on
  the texts.

### Leak audit (`audit.py`, architecture §8)

Only the shared boundary changes. Entities with `capitalised=False` were already searched both
ways; entities with `capitalised=True` keep slice 16's rule.

### Person corpus (`tools/generate_person_corpus.py`)

Three categories are appended to both languages, 20 examples each from two templates, in the
format and voice of slice 15. The generator draws them after the 14 existing categories of
**both** languages, so that the first 280 lines of each file and the existing entries of
`persons.terms.yaml` do not change. New dictionary entries are appended in order of first
appearance, English first. Each file then has 340 examples.

| Category | What the template contains | English example | Spanish example |
|---|---|---|---|
| `single` | A person named by a title and the surname, or by an initial and the surname, never by a full name; later the surname alone | `Dr. Holloway reviewed the claim. Holloway asked for the invoice.` / `Ticket from J. Holloway: the parcel is late. Holloway wants a refund.` | `Hoy ha llamado la Dra. Romero. Romero pide la factura original.` / `Incidencia de J. Romero: el paquete llega tarde. Romero quiere el reembolso.` |
| `hyphen` | A person with a hyphenated first name, later its first part alone; or a hyphenated surname (Spanish: first surname), later its second part alone | `Anne-Marie Fowler opened the ticket. Anne replied with the order number.` / `Paul Smith-Jones renewed the plan. Jones asked for a copy.` | `Ana-Belén Romero Vidal abrió la incidencia. Ana respondió con el número de pedido.` / `Federico García-Lorca Soto firmó el contrato. Lorca pide una copia.` |
| `don` | English: a person whose first name is `Don`, later `Don` or the surname alone, and once `Don't` as a common word. Spanish: `don` or `doña` (by gender) as a title before a full name, later the first name or the title with the surname | `Don't close the ticket yet: Don Fowler called about the invoice. Don wants a copy.` / `Don Fowler sent the form. Don't worry, Fowler will sign it today.` | `Atendí a don Juan Pérez García esta mañana. Juan pide la factura de marzo.` / `Doña Lucía Romero Vidal llamó ayer. La Sra. Romero quiere el reembolso.` |

- **Gold spans.** As in slice 15: a span covers the name only, without the title. An initial
  belongs to the name, so `J. Holloway` is one span. `Don`, as the English first name, is part
  of the name; `don` and `doña`, as Spanish titles, are not. `Don't` is not a span.
- **Dictionary terms.** The first mention of each person, as in slice 15, with one difference
  for `single`: the term is the title or initial with the surname (`Dr. Holloway`,
  `J. Holloway`, `Dra. Romero`), without the Spanish article. A full name is no term in
  `single`, because the person has none. In `don`, Spanish terms leave the title out
  (`Juan Pérez García`), as in every other category.
- **Persons.** Hyphenated words join two drawn words of the same kind (two first names of the
  person's gender, or two surnames) with `-`; both must pass the generator's word check. In
  English `don`, the first name is `Don`, the gender male. Persons share no name word, a
  hyphenated word counting as its parts too.
- **Invariants of slice 15 that change.** Every name word still occurs only inside gold spans,
  except the common words of `word` and `sentence` and the `Don` of `Don't`.

### Gate (`tests/eval/test_person_gate.py`)

It keeps every assertion of slice 16 and adds:

- `single`, `hyphen` and `don` join the covered categories (every letter and digit of every
  gold `PERSON` span covered, `wrong_pairs` 0) and the linked ones (`link_recall` 1.0).
- In `don`, no predicted span lies outside the gold spans: neither `Don't` nor a Spanish title
  is replaced.
- In `lower`, every letter and digit of every gold `PERSON` span is covered, except in spans
  whose name words are all common words, and `wrong_pairs` is 0. The test names the common
  words that it skips.

If a gate cannot be met, stop and report it. Change neither the corpus nor a threshold to make
it pass (AGENTS.md rule 9).

### Evaluation

- Before changing the engine, add the categories, run both commands of `docs/evaluation.md`
  with the slice 16 engine, and record the numbers of the new categories as the baseline.
- After the change, run both again. Record the person corpus numbers, by category too, next to
  the slice 16 ones. In the 14 existing categories, only `lower` may change in the dictionary
  run; with the ML layer, list every category that changed and why.
- The synthetic corpora's numbers must not change, rules only and with the ML layer. If they
  do, stop and report it.
- `expected_restoration` needs no change. If a round-trip failure appears, stop and report it.

### Documentation

- **Architecture.**
  - §2: the four terms above, replacing the slice 16 rows of name token and person.
  - §4.3: the `Don` rule, `and`, name parts, persons, forms, lower-case eligibility in 2b, and
    the term boundary in step 3.
  - §8: the boundary, and that a lower-case eligible name word is audited like any entity.
  - §10: a decision row. Decision: lower-case shorter forms are replaced unless the word is on
    a fixed list of common words. Rejected alternative: capitalised forms only, every case, or a
    list of closed-class words only. Reason: capitalised forms only forwards `thanks larry`;
    every case replaces `and` and `the brown box`; closed-class words only replaces
    `el repartidor rubio`. A gap in the list over-replaces and never leaks.
- **Usage.**
  - §6.3: lower-case forms and the common-word list, one-word names with a title or an initial,
    hyphenated names, `Don`; remove the bullets that say these are not replaced.
  - §7: replace "lower-case shorter forms (`thanks alice`) are not replaced" with: lower-case
    shorter forms are replaced unless the word is a common word, and a common word that is also
    a name (`rubio`, `brown`) is left as written in lower case; a lower-case form seen first
    restores in lower case.
  - §9: the audit sentence on capitalised words, which now applies to common words only.
- **Threat model.**
  - Risk 1: what is still sent as written: a common word that is also a name, written in lower
    case (`gracias rubio`); words shorter than `min_propagation_length`; nicknames and
    diminutives; decomposed or full-width forms of a common word; the words of a detected name
    that is written in lower case.
  - Risk 6: one-word names, name parts and `Don` add wrong links (`Mr. Smith` after
    `Dr. Smith`; `Marie` of another person after `Anne-Marie`; `Don` before another name after
    `Don Juan`). A utility loss, not a leak.
- **CHANGELOG** (`Unreleased`):
  - `Fixed`: lower-case shorter forms of a detected name, the surname after a title or an
    initial, one part of a hyphenated name, and the English first name `Don` were forwarded
    unchanged.
  - `Changed`: `Don't`, `can't` and `won't` no longer match the terms `Don`, `Can` and `Won`
    in propagation, the dictionary and the leak audit, and `and` is no longer a name word.

## Tests

Synthetic values only. Engine tests use `WordListDetector` from `tests/fakes.py` and pass
`person_tokens=True` unless the test says otherwise.

`test_common_words.py`
- Every key is `normalize_key` of itself, has no whitespace, and the set is not empty.
- It holds every name of the generator's `WORD_SURNAMES` and `WORD_FIRST_NAMES`, and `and`,
  `para`, `con`, `the`, `don`, `won`.
- Its intersection with `TITLES` is exactly `{"don", "doña"}`.

`test_names.py`
- `and` is dropped: `Rhodes, Young and Charles` → `("rhodes", "young", "charles")`.
- Every `Don` example of the "Name tokens" section, each as a test, `DON DRAPER` included.
- Every `name_parts` example, the three hyphen characters, and a part that is a title
  (`Prof-Ann` → `(("ann", "Ann"),)`).
- `is_person`: each example of the section, both ways.

`test_terms.py`
- `build_term_pattern("Don")` does not match in `Don't`, `Don’t` or `DON'T`, and matches in
  `Don's`, `Don,`, `Don.` and `Don 't` (a space before the apostrophe).
- `build_term_pattern("Can")` does not match in `can't`; `build_term_pattern("O'Connor")` still
  matches `O'Connor`.

`detectors/test_dictionary.py`
- A term `Don` is not found in `Don't`, and is found in `Don's`.

`test_audit.py`
- `Don't` in a field nobody processes is not reported for an entity `Don`, by the term pattern
  or by the folded key.
- A lower-case eligible name word (`capitalised=False`) is reported in lower case, and by its
  folded key (a decomposed accent).

`test_engine.py`
- `["Larry Thompson wrote earlier about the refund. thanks larry, got it"]` gives
  `[PERSON_1] wrote earlier about the refund. thanks [PERSON_1_B], got it`, and restoring it
  gives back the input.
- `Rose Brown … Brown says the brown box …` still leaves `brown` (on the list).
- `Juan Para Gómez … Es para mañana, dijo Para.`: `Para` is replaced, `para` is not.
- A detector that returns `larry thompson` in lower case gives no lower-case replacement of
  `larry` elsewhere, and `Larry` elsewhere is replaced.
- A lower-case occurrence before the capitalised one: `thanks larry … Larry said` gives one
  entity whose surface is `larry`.
- `Dr. Smith called. Smith wants it.`: `Smith` is `[PERSON_1_B]`. The same for `J. Smith`,
  `Mr Smith` and `Sr. Pérez`, and for a detected `Smith` (2a).
- `El cliente llamó. El cliente quiere …` with `El cliente` detected as `PERSON`: no entity for
  `cliente` or `Cliente` beyond the span.
- `Anne-Marie Fowler … Anne … Marie … Fowler`: `Anne`, `Marie` and `Fowler` are variants of
  `[PERSON_1]`. A detected `Anne Fowler` is not linked.
- `María-José` detected alone, then `José`: `José` is its variant.
- `Don Draper called. Don wants a copy, and Draper will sign it.`: `Don` and `Draper` are
  variants. `Don't worry.` next to `Don Draper` stays as written.
- `Don Juan Pérez llamó. Don Pedro no sabe nada.`: `Don` stays as written.
- `propagate=False`: no entity for a lower-case word nobody detected.
- `person_tokens=False`: the first input of this list gives
  `[PERSON_1] wrote earlier about the refund. thanks larry, got it`.
- Extend the four properties of slice 16 (round trip, no residue, append stability,
  determinism) with lower-case occurrences, one-word names with titles and initials,
  hyphenated names and `Don` names in the generator, at least 200 examples each. No residue
  now also covers lower-case eligible words: no such word of at least 3 characters is left on
  its own in the output. Check, as slice 16 did, how often the generator reaches each new case,
  and record it in the pull request.

`proxy/test_app.py`
- With a dictionary term `Larry Thompson` (type `PERSON`), an OpenAI request with
  `Larry Thompson wrote. thanks larry` reaches the mocked upstream as
  `[PERSON_1] wrote. thanks [PERSON_1_B]`, and the response `"[PERSON_1_B]"` reaches the
  client as `larry`.

`tests/eval/test_person_corpus.py`
- 17 categories of 20 examples per language, 340 examples per file.
- The first 280 lines of each file and the first entries of `persons.terms.yaml` are those of
  slice 15. Pin them with their SHA-256, computed before this slice changes the generator.
- The new categories contain what the table says: in `single` no full name, in `hyphen` a
  hyphenated name word, in English `don` the first name `Don` and a `Don't`, in Spanish `don`
  the title before a full name.

`tests/eval/test_person_gate.py`
- The gates of the "Gate" section.

## Acceptance checklist

- [ ] With `person_tokens` off, every pre-existing test passes with unchanged expectations,
      apart from any test that matched a term in front of `'t`. With the configuration default
      (on), every test whose expectation changed is listed in the pull request, with the
      reason.
- [ ] The baseline of the new categories (slice 16 engine) and the new numbers are recorded in
      `docs/evaluation.md`. The synthetic corpora's numbers are unchanged.
- [ ] The person gate passes, new categories included.
- [ ] The four properties hold with the new cases, with at least 200 Hypothesis examples each,
      and each ran once with 20,000 examples. The pull request records it.
- [ ] Mutation checks: break each condition on purpose and confirm a test fails. The
      conditions are the common-word check, the written-form check of eligibility, the `and`
      particle, each condition of the `Don` rule, the title-or-initial condition of `is_person`,
      the two-part condition, parts kept out of runs, the `'t` boundary in each of the four
      searches, and the generator's order (existing lines unchanged). The pull request lists
      them.
- [ ] Architecture, usage, threat model, evaluation and CHANGELOG are updated in the same pull
      request.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Nicknames, diminutives and transliterations.
- Linking the words of other types, such as `ORG`.
- Common words of other languages, and a setting to extend the list.
- Name words with an inner `.` (`S.A.` in an ML span of a company).
- Replacing a run of name words as one span, so that the space between them is replaced too.
- Changes to detectors other than the dictionary's boundary, to the resolver, placeholders,
  restoration or streaming.

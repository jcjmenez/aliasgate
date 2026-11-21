# Slice 16: person name tokens and linking

## Goal

Once a person's full name is detected, the shorter forms of that name are protected and linked
to it. A first name, a surname or a run of the name ("Johnson", "Sr. Pérez", "Juan Pérez" for
"Juan Pérez García") that stands alone in the request is replaced as a variant of that person
(`[PERSON_1_B]`), even when no detector found it, provided it starts with an upper-case letter.
A shorter form that a detector did find is linked in the same way, instead of getting a number
of its own.

Today these forms reach the provider unchanged unless a detector finds them. When one does, the
form gets an unrelated number, so the model reads one person as two. The person corpus of
[slice 15](slice-15-person-evaluation.md) measures both problems, and this slice gates them in
CI.

This is the second of two slices for backlog §1, part 2. It builds on the links and variant
letters of slice 13.

## Depends on

Slice 15 (the person corpus, its link scores and its baseline). Every earlier slice is merged.

## Decisions taken with the maintainer before implementation (2026-11-21)

1. **Shorter forms nobody detected are replaced, where they are capitalised.** A word of a
   person's name that stands alone elsewhere in the request becomes a variant of that person.
   It is replaced only where it starts with an upper-case letter, so `la mesa` stays as written
   next to `Pedro Mesa`, and so does `it will` next to `Will Stone`. Rejected: replacing every
   case, as propagation does, which turns common words that are also names into placeholders;
   and not replacing them, which keeps the leak.
2. **Which forms link.** One name word in any position, or a contiguous run of name words.
   Titles, particles and initials do not count. Rejected: only the first or the last word,
   which misses the Spanish first surname (`Pérez` in `Juan Pérez García`, the surname people
   use); and any ordered subset, which links `Juan García` to `Juan Pérez García`, most likely
   another person.
3. **A word of two persons gets a number of its own.** A word that the names of two or more
   persons contain, among the persons known when the word is first seen, is not linked. It
   still gets replaced. Persons that appear later in the request never change an earlier
   decision: that keeps append stability (architecture §4.3).
4. **On by default.** `detection.person_tokens: true`. Without propagation, only spans that a
   detector found are linked, because the shorter forms nobody detected are replaced by the
   propagation step.

## Decisions made while writing this spec

- **The engine keyword defaults to off, the configuration to on.** `Pseudonymizer(...,
  person_tokens=False)` keeps every existing engine test and every library caller unchanged.
  The gateway, `aliasgate check`, `aliasgate demo` and the evaluation harness build the engine
  from the configuration, so they get the new behaviour by default.
- **Words nobody detected are looked for once per text.** After the spans of a text are
  assigned, the engine looks for the words of the persons known so far, in that text and in the
  earlier ones. This granularity sees two persons that share a surname in one message, and a
  conversation that grows by whole texts keeps every earlier entity unchanged.
- **Measured with a prototype** (2026-11-21, ML on, synthetic texts, outside the repository).
  The prototype replaced and linked every capitalised shorter form of the texts described in
  slice 15. It gave a shared surname its own number, and left `la mesa`, `la rosa` and
  `it will` unchanged when capitalised-only matching was on. It also replaced `Will` in
  `Will you check it?`, which is the expected false positive of decision 1.

## Dependencies to add

None.

## Files

| File | Action |
|---|---|
| `src/aliasgate/names.py` | new: name tokens of a person's surface |
| `src/aliasgate/mapping.py` | change: `variant_of`, `capitalised`, letters counted per family |
| `src/aliasgate/engine.py` | change: linking of detected spans, words nobody detected, capitalised propagation |
| `src/aliasgate/audit.py` | change: capitalised entities |
| `src/aliasgate/config.py`, `src/aliasgate/factory.py` | change: `detection.person_tokens` |
| `src/aliasgate/cli.py` | change: one line in `check` |
| `tests/test_names.py` | new |
| `tests/test_mapping.py`, `tests/test_engine.py`, `tests/test_audit.py`, `tests/test_config.py`, `tests/test_factory.py`, `tests/test_cli.py`, `tests/proxy/test_app.py` | change |
| `tests/eval/test_person_gate.py` | new: the CI gate on the person corpus |
| `docs/architecture.md` | change: §2, §4.1, §4.2, §4.3, §8, §10 |
| `docs/usage.md` | change: §3 (`check` output), §6.3, §7 |
| `docs/threat-model.md` | change: risks 1, 6 and 7 |
| `docs/evaluation.md` | change: the person corpus numbers with this slice |
| `examples/aliasgate.example.yaml` | change: `person_tokens: true` |
| `CHANGELOG.md` | change: `Unreleased` → `Fixed` and `Changed` |

## Specification

Read architecture §4 and §8 and slice 13's "Mapping" section before starting. This slice changes
contracts defined there, so the architecture is updated in the same pull request (AGENTS.md
rule 6).

### Vocabulary (architecture §2)

| Term | Meaning |
|---|---|
| **Name token** | A word of a `PERSON` surface that can name the person on its own: not a title, a particle or an initial (see below). |
| **Person** | A `PERSON` entity whose surface has at least two name tokens. |
| **Family** | The entities that share a type and a number: an entity and its variants. |

### Name tokens (`names.py`, new, core)

```python
TITLES: Final[frozenset[str]]
PARTICLES: Final[frozenset[str]]

def name_tokens(surface: str, *, min_length: int) -> tuple[str, ...]:
    """The keys of the words of a person's surface that can name the person on their own, in
    order."""
```

1. Split the surface on whitespace.
2. Strip from both ends of each word every character whose Unicode category starts with `P`
   (punctuation), so `(Johnson),` gives `Johnson`. An inner apostrophe or hyphen stays:
   `O'Connor` and `María-José` are one word each.
3. Take `normalize_key` of the word.
4. Drop the word if its key is empty, if its key with one final `.` removed is in `TITLES`, if
   its key is in `PARTICLES`, if it is an initial (one letter, with or without a final `.`), or
   if its key is shorter than `min_length`.

`TITLES` holds `mr`, `mrs`, `ms`, `miss`, `mx`, `dr`, `prof`, `sir`, `dame`, `lady`, `lord`,
`sr`, `sra`, `srta`, `sres`, `sras`, `don`, `doña`, `d`, `dª`, `dña`, `dra`, `señor`,
`señora`, `señorita`, `lic`, `lcdo`, `lcda` and `ing`. `PARTICLES` holds `de`, `del`, `la`,
`las`, `los`, `y`, `e`, `da`, `das`, `do`, `dos`, `di`, `du`, `van`, `von`, `der`, `den`,
`ter`, `le`, `el`, `al`, `bin`, `ibn`, `san` and `santa`. Both are module-level frozensets of
keys. The English first name `Don` is a title in this list. That is a known miss, which
`docs/usage.md` mentions.

Examples: `Juan Pérez García` → `("juan", "pérez", "garcía")`. `Dña. Lucía Romero` →
`("lucía", "romero")`. `María del Mar Rosa` → `("maría", "mar", "rosa")`. `J. R. Smith` →
`("smith",)`. `Sr.` → `()`.

The function is pure and does no I/O. Only the engine uses it.

### Mapping (`mapping.py`, architecture §4.2)

```python
@dataclass(frozen=True, slots=True)
class Entity:
    key: str = field(repr=False)
    entity_type: str
    surface: str = field(repr=False)
    placeholder: str
    variant: str | None = None
    capitalised: bool = False  # propagated and audited only where it starts upper-case

class Mapping:
    def assign(self, entity_type: str, surface: str, *, key: str | None = None,
               link: str | None = None, variant_of: Entity | None = None,
               capitalised: bool = False) -> Entity: ...
```

`assign` decides in this order. Rule 2 is new. Rules 1 and 3 to 6 are rules 1 to 5 of slice 13:

1. **Known key.** The key already has an entity: return it unchanged, whatever `link`,
   `variant_of`, `capitalised` and `entity_type` are.
2. **Variant of.** `variant_of` is given: the new entity joins the family of that entity, with
   its number and the family's next unused letter of `B` to `Z`. When the family has used all
   25 letters, the new entity gets the next number and no letter. `variant_of` together with a
   `link`, with another `entity_type`, or naming an entity that is not in this mapping, raises
   `ValueError`. The message does not repeat any value.
3. **No link.** Create an entity with the next number for the type and no variant.
4. **New group.** As before.
5. **Known group.** As before.
6. **Full group.** As before.

- Letters are counted per family, by type and number. A link group is a family whose number is
  recorded for its link, so a dictionary term, its aliases and its person's name tokens share
  one set of 25 letters. The group table keeps link ids only as its keys, as before.
- `capitalised` is stored on the new entity and never changes. `repr` still shows only the type
  and the placeholder, and pickling and copying still raise (security invariant 3).

### Engine (`engine.py`, architecture §4.1 and §4.3)

```python
class Pseudonymizer:
    def __init__(self, detectors: Sequence[Detector], *, allow_list: Iterable[str] = (),
                 propagate: bool = True, min_propagation_length: int = 3,
                 person_tokens: bool = False) -> None: ...
```

With `person_tokens` off, nothing below applies, and every output is that of slice 15. With it
on, step 2 of architecture §4.3 runs text by text, as follows. Name tokens are always computed
with `min_length=min_propagation_length`, and a token whose key is in the allow list is never a
name token.

**2a. Linking a detected span.** Walk the resolved spans of the text by `start`, as today. A
span is linked when all of these hold:

- its type is `PERSON` and it has no link;
- its key has no entity yet;
- its name tokens are not empty;
- they form a contiguous run of the name tokens of at least one person that exists at that
  point of the walk, and all such persons belong to one family. Equal sequences count, so
  `Dr. Alice Johnson` links to `Alice Johnson`.

A linked span is assigned with `variant_of` set to the first entity of that family, in order of
assignment. Any other span is assigned as today. A dictionary span with a link keeps its group. The persons of a
family are its entities that have at least two name tokens, whether they came from a detector
or from a dictionary.

**2b. Words nobody detected.** This step runs only with `propagate` on. After the walk of text
`i`, the candidates are the keys of the name tokens of every person that exists, excluding keys
that already have an entity, of any type. A candidate occurs on its own at a match of
`build_term_pattern(word)` in one of the texts `0..i` when three things hold: the match starts
with an upper-case or title-case letter (`str.isupper` or `str.istitle` of its first
character), its `normalize_key` equals the candidate, and it overlaps no span resolved for that
text in step 1. Here `word` is the token as written, after stripping, in the surface of the
first person that has it. Then:

- Take the candidates that occur on their own somewhere in texts `0..i`. Order them by their
  first such occurrence (text index, then start), with ties broken by key.
- In that order, assign each one with `capitalised=True` and the surface of its first
  occurrence. When the persons of exactly one family have the token, assign it with
  `variant_of` set to that family's first entity. Otherwise, assign it with no link: it gets
  the next number.
- A candidate that does not occur on its own stays pending. It may be assigned after a later
  text.
- Each candidate is searched in each text at most once. A candidate that first appears after
  text `i` is searched in texts `0..i`, and after that only in each new text. The work is then
  proportional to candidates × texts, as propagation's is to entities × texts. Searching every
  pending candidate in every earlier text after each text would be cubic in a long
  conversation.

**3. Propagation.** As today, longest canonical surface first. An entity with `capitalised`
keeps only the matches that start with an upper-case or title-case letter.

Notes:

- Numbering still depends only on the content and order of the texts. Step 2b depends on the
  spans resolved in step 1 and on the text, never on which ranges propagation claims, so an
  entity that exists after text `i` exists, with the same placeholder, in every longer batch.
  Update the numbering sentence of §4.3: numbers and letters follow the order of first
  detection or, for a name token that nobody detected, the order of its first occurrence on its
  own once its person exists.
- Occurrences found in step 3 keep source `"propagation"`, so `BatchStats`, the metrics and
  `analyze` need no change.
- The order of `mapping.entities()` is the order of assignment, as before.
- A name token's entity may exist without any replaced occurrence. This happens when its only
  occurrence on its own lies inside a longer value that propagation replaces first. Such an
  entity is still audited, and it counts in `x-aliasgate-entities`.

### Leak audit (`audit.py`, architecture §8)

- An entity with `capitalised` is found only by its term pattern, at a match that starts with
  an upper-case or title-case letter.
- Its key is not searched in the folded text. A folded search would report the lower-case
  common word that the gateway leaves in place on purpose (`la mesa`). Consequence: a
  decomposed accent or full-width letters in such a word are not caught. These words were not
  entities at all before this slice, so this is no regression. Record it in the threat model
  (risk 1).
- The single-pass alternations may include capitalised entities in the term alternation as a
  pre-filter. They must leave them out of the key alternation. The per-entity check decides.

### Configuration, factory and CLI

- `DetectionSettings.person_tokens: bool = True`. The environment variable is
  `ALIASGATE_DETECTION__PERSON_TOKENS`.
- `build_pseudonymizer` passes it to the engine.
- `aliasgate check` prints `person tokens: on` or `off` after the `propagation:` line. Update
  the sample output in usage §3.
- `examples/aliasgate.example.yaml`: `person_tokens: true` after `propagate`, with a short
  comment.

### Evaluation

- `expected_restoration` and the family lookup of slice 15 need no change: a capitalised
  entity's pattern matches the spans that propagation gave it. If a round-trip failure appears,
  stop and report it.
- Re-run both baseline commands of slice 15 and record the new numbers next to the old ones in
  `docs/evaluation.md`. The numbers of the synthetic corpora must not change. Check this with
  the CI gate and with the ML run.

### Documentation

- **Architecture.**
  - §2: the three terms above.
  - §4.1: the `person_tokens` keyword.
  - §4.2: the six `assign` rules, families, and `capitalised`.
  - §4.3: steps 2a, 2b and 3, and the numbering sentence.
  - §8: the audit row and the paragraph on capitalised entities.
  - §10: a decision row. Decision: shorter forms of a person's name become variants, replaced
    only where capitalised. Rejected alternative: case-insensitive matching, or no replacement.
    Reason: case-insensitive matching turns common words that are also names into
    placeholders, and no replacement forwards the shorter forms.
- **Usage.**
  - §3: the `check` line.
  - §6.3: `person_tokens`: what it does, that it needs a detected full name, that it needs
    propagation for undetected shorter forms, and how to turn it off.
  - §7: these bullets:
    - shorter forms of a name are linked (`[PERSON_1_B]`) and restored as written;
    - a capitalised word at the start of a sentence that equals a name word is replaced too
      (`Will you…` next to `Will Stone`);
    - lower-case shorter forms (`thanks alice`) are not replaced;
    - in a later message, a different person who shares a word with an earlier one gets the
      earlier person's variant;
    - an organisation named after a person (`Vidal Logistics`) may show that person's variant.
  - §7, "Preserved thinking": a later turn that names a person can change an earlier message
    that held a shorter form of the name. This is the same exception as propagation.
- **Threat model.**
  - Risk 1: lower-case shorter forms, words shorter than `min_propagation_length`, the title
    `Don` as a first name, and decomposed or full-width forms of a capitalised word are not
    caught.
  - Risk 6: a wrong link misleads the model about who did what. It is a utility loss, not a
    leak.
  - Risk 7: the provider sees which placeholders are shorter forms of one person.
- **CHANGELOG** (`Unreleased`):
  - `Fixed`: shorter forms of a detected person's name, such as a first name, a surname or a
    title with a surname, were forwarded unchanged.
  - `Changed`: a shorter form that a detector found is now a variant of the full name, which
    changes the numbering, and `detection.person_tokens` turns this off.

## Tests

Use synthetic values only, from the shared test data and from slice 15's word lists. Engine
tests use `WordListDetector` from `tests/fakes.py` with the full names as `PERSON` words. It
finds them case-sensitively. They pass `person_tokens=True` unless the test says otherwise.

`test_names.py`
- The examples of the "Name tokens" section, each as a test.
- Titles with and without the dot (`Sr.`, `Sr`, `Ms`), in any case.
- Every word of `TITLES` and `PARTICLES` is dropped. A word that merely contains one (`Delia`,
  `Donald`) is kept.
- `min_length` drops shorter words, and `Li` is dropped with the default of 3.
- Punctuation at the ends is stripped, while an inner apostrophe and hyphen are kept.
- Keys are NFKC and case-folded (`ＪＯＨＮＳＯＮ` → `johnson`).

`test_mapping.py`
- `variant_of` an unlinked entity gives `[PERSON_1_B]`, then `[PERSON_1_C]`.
- `variant_of` an entity of a dictionary group continues the group's letters. The group's
  aliases and the tokens share 25 letters, and the 26th distinct surface of the family gets the
  next number with no letter.
- `variant_of` a variant joins the same family.
- A known key ignores `variant_of` and `capitalised`.
- `variant_of` with a `link`, with another type, or with an entity of another mapping raises
  `ValueError`, and the message holds no surface.
- `capitalised` defaults to False, is stored, and is absent from `repr`.
- Extend the uniqueness property with `variant_of`: every placeholder is unique and resolves to
  its own surface.

`test_engine.py`
- `["Alice Johnson opened the ticket. Ms. Johnson replied. Alice called."]` gives
  `[PERSON_1] opened the ticket. Ms. [PERSON_1_B] replied. [PERSON_1_C] called.`, and restoring
  it gives back the input.
- The Spanish first surname: `Juan Pérez García … el Sr. Pérez …` gives `[PERSON_1_B]` for
  `Pérez`.
- A detected shorter form links: when the fake also finds `Pérez`, it is `[PERSON_1_B]`, not
  `[PERSON_2]`. The same holds for a detected run (`Juan Pérez` after `Juan Pérez García`) and
  for a detected form with a title (`Dr. Alice Johnson` after `Alice Johnson`).
- A detected non-contiguous form (`Juan García` after `Juan Pérez García`) does not link.
- `Rose Brown … Brown says the brown box …`: `Brown` is `[PERSON_1_B]`, and `brown` stays.
- A name word that occurs only in lower case creates no entity, and the mapping has one entity.
- `Will Stone … Will you check it?`: `Will` is replaced. This test documents decision 1.
- One text with `Bob Smith` and `Alice Smith`, then `Smith`: `Smith` gets its own number, and
  `Bob` and `Alice` link to their persons.
- Two texts: `Bob Smith … Smith` and then `Alice Smith … Smith`. The second `Smith` is still
  `[PERSON_1_B]`: the earlier person wins, as append stability requires.
- A shorter form before the full name in the same text is linked.
- A shorter form in text 1 and the full name in text 2: in the two-text batch, the shorter form
  in text 1 is replaced with the variant. In a batch of text 1 alone, it is not.
- Titles, particles, initials and short words never become entities: `Sr.`, `del`, `J.` and
  `Li`, each standing alone next to a full name that contains it.
- An allow-listed name word is not replaced.
- A name word whose key already belongs to an `ORG` entity stays that entity.
- `propagate=False`: no entity is created for a word nobody detected, and a detected shorter
  form still links.
- `person_tokens=False`: the first input of this list gives
  `[PERSON_1] opened the ticket. Ms. Johnson replied. Alice called.`, as before this slice, and
  a detected `Pérez` after `Juan Pérez García` is `[PERSON_2]`.
- A dictionary person with an alias: the tokens of the term take letters after the aliases
  seen so far.
- `LITERAL` spans are unaffected: `[PERSON_1]` in the input next to a full name stays a
  literal.
- property (round trip): with `person_tokens` and propagation on, `restore(pseudonymize(texts))`
  equals `expected_restoration` from `aliasgate.eval.metrics`, with at least 200 examples. The
  generator mixes full names from a word list, their words alone in random case, and filler.
- property (no residue): no word of a person that is at least 3 characters long and starts
  upper-case is left on its own in the output.
- property (append stability): for batches `A` and `A + B`, every entity of `mapping(A)` keeps
  its placeholder, variant, surface and `capitalised` flag in `mapping(A + B)`.
- property (determinism): two runs give identical texts and mappings.
- Search bound: 1,000 texts, each with a new two-word person whose words never stand alone, so
  that every candidate stays pending, run in under 15 s. Check that a version that searches
  every pending candidate in every earlier text after each text exceeds that bound, and record
  both timings in the pull request (slice 14 did the same for PEM keys).

`test_audit.py`
- A capitalised entity is reported when its word, capitalised, is in a field nobody processes.
- It is not reported for the lower-case word (`la mesa`) or for a folded form.
- Other entities are audited exactly as before.

`test_config.py`, `test_factory.py`, `test_cli.py`
- `person_tokens` defaults to True and can be set from the file and from the environment.
- The factory passes it on: with it on and off, a request with `Alice Johnson … Alice` gives
  the two outputs.
- `check` prints the new line.

`proxy/test_app.py`
- With a dictionary term `Alice Johnson` (type `PERSON`, no ML), an OpenAI request with
  `Alice Johnson wrote. Alice will call.` reaches the mocked upstream as
  `[PERSON_1] wrote. [PERSON_1_B] will call.`. The response `"[PERSON_1_B] and [PERSON_1]"`
  reaches the client as `Alice and Alice Johnson`, and `x-aliasgate-entities` is `2`.
- The logging invariant test still passes with a request that contains shorter forms.

`tests/eval/test_person_gate.py`, the CI gate. It runs with `tests/eval/corpus/persons.yaml`
and the default configuration otherwise, so `person_tokens` is on. It groups examples by the
category in their ids. For each language:

- In `full`, `first`, `last`, `title`, `before`, `run`, `possessive`, `word`, `shared-last` and
  `shared-first`, every letter and digit of every gold `PERSON` span is covered by a predicted
  span. Whitespace between two replaced words of a run does not count, so compute this in the
  test rather than with the leak rate.
- In those categories, `wrong_pairs` is 0.
- In `full`, `first`, `last`, `title`, `before`, `run`, `possessive` and `word`, `link_recall`
  is 1.0.
- In `word`, no predicted span lies outside the gold spans: the lower-case word is not
  replaced.
- Over the whole corpus, there are no round-trip failures.

The remaining categories are measured and documented, not asserted. `other` gives wrong merges,
`sentence` a false positive, `lower` a leak, and in `org` the surname inside the organisation
name is replaced as the person. If the implementation cannot meet a gate, stop and report it.
Change neither the corpus nor a threshold to make it pass (AGENTS.md rule 9).

## Acceptance checklist

- [ ] With `person_tokens` off, every pre-existing test passes with unchanged expectations.
      With the configuration default (on), every test whose expectation changed is listed in
      the pull request, with the reason. The change must be exactly the effect of this slice.
- [ ] The person gate passes, and the slice 15 numbers and the new ones are both recorded in
      `docs/evaluation.md`. The synthetic corpora's numbers are unchanged.
- [ ] Round trip, no residue, append stability and determinism hold with name tokens, with at
      least 200 Hypothesis examples each. Each property was run once with 20,000 examples, and
      the pull request records it.
- [ ] Mutation checks: break each condition on purpose and confirm a test fails. The
      conditions are the capitalisation filter in propagation and in the audit, the
      exactly-one-family rule, the contiguous-run rule, each word list, the allow list, the
      per-text pass, and the once-per-text search. The pull request lists them.
- [ ] Architecture, usage, threat model, evaluation, example configuration and CHANGELOG are
      updated in the same pull request.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Linking any type other than `PERSON`, for example `ORG` words (`ACME` for `ACME
  Corporation` stays a dictionary alias).
- Lower-case shorter forms, nicknames (`Bob` for `Robert`), diminutives (`Juanito`) and
  transliterations.
- Telling the model what a variant letter means (backlog §15).
- Splitting a hyphenated name (`José` for `María-José`).
- Changes to detectors, the resolver, placeholders, restoration or streaming.

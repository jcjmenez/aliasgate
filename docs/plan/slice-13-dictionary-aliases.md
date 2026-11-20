# Slice 13: dictionary aliases and variant placeholders

## Goal

A dictionary entry can list aliases, for example `ACME` for `ACME Corporation`. All the surfaces
of one entry share one entity number, and each surface gets its own variant letter: `[ORG_1]`
for the first surface seen, `[ORG_1_B]` for the next one, and so on. The model can tell that
they name one organisation, and restoration stays lossless, because each placeholder gives back
its own surface. Without aliases nothing changes: every output of v0.1.0 is reproduced byte for
byte.

This is the first half of backlog §1 (entity linking). Linking `PERSON` surfaces by their
tokens ("Pérez" for "Juan Pérez") stays in the backlog. It builds on the `link` and the variants
introduced here.

## Depends on

Slice 12 (v0.1.0 released). Every earlier slice is merged.

## Decisions taken with the maintainer before implementation (2026-11-20)

1. **Scope: aliases only.** Linking by tokens needs an evaluation set for wrong merges, which
   are worse than missed merges. It is specified after this slice has been measured.
2. **Format `[TYPE_N_V]`.** `V` is one upper-case letter from `B` to `Z`, and the first surface
   has no letter. The variant uses the separators the tolerant pattern already accepts before
   the number. Rejected: `[ORG_1B]`, which a model can read as another number; and one
   placeholder for every surface restored as the first one, which loses the user's wording and
   breaks the round trip.
3. **Letters follow first appearance**, as numbers already do (architecture §4.3): the first
   surface seen in the batch is `[ORG_1]`, whether it is the term or an alias. This keeps append
   stability, and the provider cannot tell how many aliases the dictionary holds. Rejected: one
   fixed letter per alias in dictionary order. With it, a request that only mentions the third
   alias shows `[ORG_1_C]` with no `B`.
4. **Shared surfaces are an error.** A term or alias whose key also belongs to another entry
   stops the start-up, and the message names entry positions, never the text. Rejected: the
   first entry wins.

## Decisions made while implementing

- **Links only on entries with aliases** (asked and answered on 2026-11-20). An entry without
  aliases gives spans without a link, as in v0.1.0. This is what keeps the two compatibility
  promises of this file: every pre-existing test passes unchanged (the dictionary tests compare
  whole spans), and the output stays byte for byte that of v0.1.0. With a link on every entry,
  `İvan Petrov` and `Ivan Petrov` in one batch, which the dictionary matches as one term but
  `normalize_key` keeps apart, would change from `[PERSON_1]` and `[PERSON_2]` to `[PERSON_1]`
  and `[PERSON_1_B]`.
- **Conflict messages.** When two entries have the same term key and different types, the
  v0.1.0 message is kept (`terms 1 and 3 are the same term with different entity types`),
  whether or not they have aliases. When they have the same term key and type and neither has
  aliases, the later one is ignored, as in v0.1.0. Any other key in two entries gives
  `terms 1 and 3 share a term or alias`. An entry is checked on its own first (empty term,
  type, the limit, empty or repeated aliases), then against the earlier entries.
- **Configuration.** `aliases: null` and a list with a non-string item get the message for a
  value that is not a list of strings. The loader rejects empty aliases with the entry position,
  so the detector's own check only matters for terms built in code.
- **Mapping.** `assign` builds the entity, and with it the placeholder, before it changes any
  counter or group, so a failure leaves the mapping as it was.
- **Manual check (2026-11-20).** One request named `ACME Corporation`, `ACME`, `Project Falcon`
  and `Falcon`, with the last two as term and alias. It went through `aliasgate serve` and a
  local relay that recorded what was forwarded.
  - The provider received `[ORG_1] signed the [PROJECT_1] contract on Monday. On Friday,
    [ORG_1_B] asked to move [PROJECT_1_B] to March.`
  - Claude Sonnet 5.5 on Microsoft Foundry answered with all four placeholders as given,
    variant letters included. The client read `ACME Corporation signed the Project Falcon
    contract on Monday. On Friday, ACME asked to move Falcon to March.`
  - A first try stopped at `max_tokens`, after 68 thinking tokens, in the middle of
    `[PROJECT_`. That fragment is not a placeholder, so it reached the client unchanged and was
    not counted as unresolved.
  - The first two requests to Azure OpenAI `gpt-4.1` failed at the TLS handshake, before they
    reached the API. This is a known problem of the test machine, and the gateway answered
    502. Later the same day, after slice 13 was merged, a free models call showed that the
    handshake worked again. The streamed request was then repeated through the merged code.
    `gpt-4.1` answered with all four placeholders as given. Each placeholder arrived split
    across five or six stream chunks (`[`, `ORG`, `_`, `1`, `_B`, `]`). The client read `ACME
    Corporation signed the Project Falcon contract on Monday. On Friday, ACME requested to move
    Falcon to March.`
  - The gateway logs held counts only. The total spend for both routes was about USD 0.004.

## Dependencies to add

None.

## Files

| File | Action |
|---|---|
| `src/aliasgate/placeholders.py` | change: variant in the format, the tolerant pattern and the parser |
| `src/aliasgate/types.py` | change: `Span.link` |
| `src/aliasgate/resolve.py` | change: the link as the last tie-break |
| `src/aliasgate/mapping.py` | change: link groups and variant letters |
| `src/aliasgate/engine.py` | change: pass links to the mapping, restore variants |
| `src/aliasgate/detectors/dictionary.py` | change: aliases and links |
| `src/aliasgate/config.py` | change: optional `aliases` key in dictionary files |
| `tests/fakes.py` | change: `WordListDetector` accepts links |
| `tests/test_placeholders.py`, `tests/test_types.py`, `tests/test_resolve.py`, `tests/test_mapping.py`, `tests/test_engine.py`, `tests/test_restore.py`, `tests/test_stream.py`, `tests/test_audit.py`, `tests/test_config.py`, `tests/detectors/test_dictionary.py`, `tests/proxy/test_app.py` | change |
| `docs/architecture.md` | change: §2, §3.1, §3.3, §4.2, §4.3, §5.1, §5.2, §5.3, §10 |
| `docs/usage.md` | change: §5 (aliases), §7 (what users notice), §10 (`x-aliasgate-entities`) |
| `docs/threat-model.md` | change: residual risk 7 |
| `examples/dictionary.example.yaml` | change: one alias |
| `CHANGELOG.md` | change: an `Unreleased` entry |

## Specification

Read architecture §3, §4 and §5 before starting. This slice changes contracts defined there, so
the architecture is updated in the same pull request (AGENTS.md rule 6). The changes below are
what the updated sections must say.

### Placeholders (`placeholders.py`, architecture §5.1 and §5.2)

```python
VARIANT_LETTERS: Final = "BCDEFGHIJKLMNOPQRSTUVWXYZ"

def format_placeholder(entity_type: str, number: int, variant: str | None = None) -> str:
    """Return the canonical placeholder: "[ORG_1]" without a variant, "[ORG_1_B]" with one."""

def parse_match(match: re.Match[str]) -> tuple[str, int, str | None]:
    """Return (entity type, number, variant) for a match of TOLERANT. Backslashes are removed
    from the type, the type and the variant are upper-cased, and the variant is None when the
    match has none."""
```

- `variant` must be exactly one character of `VARIANT_LETTERS`. Anything else (`"A"`, a lower
  case letter, two letters, a digit, `""`) raises `ValueError`, and the message does not repeat
  the value.
- The tolerant pattern gains an optional variant after the number:

  ```python
  _BODY = (
      r"[ \t]{0,2}([A-Za-z](?:\\?_|[A-Za-z]){0,39}?)(?:\\?_| )(\d{1,9})"
      r"(?:(?:\\?_| )([A-Za-z]))?[ \t]{0,2}"
  )
  TOLERANT = re.compile(rf"\\\[{_BODY}\\\]|\[{_BODY}\]")
  ```

  The groups are 1–3 for the escaped brackets and 4–6 for the plain ones. The variant accepts
  an underscore, a Markdown-escaped underscore or one space before it, and either case. The
  pattern accepts every letter, `A` included: `[ORG_1_A]` is then found and counted as
  unresolved, where an unmatched form would pass unseen.
- The bounds do not change. The longest canonical placeholder is 54 characters
  (`MAX_PLACEHOLDER_LENGTH = 64`), and the longest tolerant match is 101 (`MAX_TOLERANT_LENGTH =
  128`). Update the computation in the comments and in the bound test.
- Every match of the v0.1.0 pattern is still a match, at the same offsets, with no variant.
  The new pattern also finds bracketed text that ends in a separator and one letter after a
  number, such as `[Step 1 a]`. In a request, such text becomes a `LITERAL` placeholder, like any
  placeholder-shaped text, and is restored exactly. Document this in usage §7.

### Spans (`types.py`, architecture §3.1)

```python
@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int
    entity_type: str
    score: float
    source: str
    priority: int
    link: str | None = None
```

- `link` groups the spans of different surfaces that name one entity. It is an opaque id
  chosen by the detector: the detector's name, a colon and a decimal number, as in
  `dictionary:7`. It must match `[a-z][a-z0-9_]*:[0-9]{1,9}` (compiled once, `fullmatch`), or
  construction raises `ValueError` with a message that does not repeat the value. The format
  makes it impossible for a link to carry text.
- In this slice only the dictionary sets links. Rule, ML and literal spans keep `None`. The
  detection cache stores spans with their link unchanged.

### Overlap resolution (`resolve.py`, architecture §3.3)

- The sort key gets `span.link or ""` as its last element, so that the order stays total and
  the result still does not depend on the order of the input.

### Mapping (`mapping.py`, architecture §4.2)

```python
@dataclass(frozen=True, slots=True)
class Entity:
    key: str = field(repr=False)
    entity_type: str
    surface: str = field(repr=False)
    placeholder: str
    variant: str | None = None  # None for the first surface of a group and for unlinked entities

class Mapping:
    def assign(self, entity_type: str, surface: str, *, key: str | None = None,
               link: str | None = None) -> Entity: ...
    def lookup(self, entity_type: str, number: int,
               variant: str | None = None) -> str | None: ...
```

`assign` decides in this order:

1. **Known key.** The key already has an entity: return it unchanged, whatever `link` and
   `entity_type` are. This is v0.1.0 behaviour. The entity does not join the link's group.
2. **No link.** Create an entity with the next number for the type and no variant (v0.1.0
   behaviour).
3. **New group.** A link is given and the group `(link, entity_type)` has no entity yet: create
   an entity with the next number and no variant, and record the number for the group.
4. **Known group.** The group exists and has used fewer than 25 variants: create an entity with
   the group's number and the next unused letter of `VARIANT_LETTERS`.
5. **Full group.** The group has used all 25 letters: create an entity with the next number and
   no variant, outside the group. Nothing leaks; the surface is only not linked. With the
   dictionary's limit of 25 aliases this needs keys that the dictionary's case-insensitive
   matching treats as one term but `normalize_key` keeps apart, such as `İ` and `i`.

Other rules:

- A group is keyed by `(link, entity_type)`, so a link never joins entities of different types.
- `lookup(type, number)` without a variant returns the surface of the entity that has no
  letter; with a variant it returns that variant's surface. In both cases it returns None when
  the placeholder is not in the mapping.
- `entities()` and `len()` include variants, because every placeholder is one entity. The
  `x-aliasgate-entities` header therefore counts placeholders, variants included. Document this.
- Link ids are kept only as keys of the internal group table. `repr`, pickling and copying
  behave as before (security invariant 3).

### Engine (`engine.py`, architecture §4.3 and §5.3)

- Step 2 calls `mapping.assign(span.entity_type, surface, link=span.link)` for every
  non-`LITERAL` span. `LITERAL` spans keep their exact key and ignore links.
- Propagation does not change. Variants are entities, so each variant's surface is propagated
  (longest canonical surface first, as before) and audited like any other entity.
- `restore` and `count_unresolved` look up `(type, number, variant)`. An unknown variant of a
  known number, such as `[ORG_1_F]`, is unresolved and left as it is (security invariant 6).
- `analyze` returns spans with their link.
- `audit.py` and `stream.py` need no change. The stream restorer depends only on the brackets
  and on `MAX_TOLERANT_LENGTH`. Its property tests must still be run with variant placeholders
  (see Tests).

### Dictionary (`detectors/dictionary.py`)

```python
@dataclass(frozen=True, slots=True)
class Term:
    term: str = field(repr=False)
    entity_type: str
    aliases: tuple[str, ...] = field(default=(), repr=False)

MAX_ALIASES: Final = 25  # the term and its aliases get 26 distinct placeholders: no letter, B-Z
```

- An alias is matched exactly like a term: as a whole term, ignoring case, with any whitespace
  between its words. Terms and aliases are in the same alternation, longest first. A match of
  the term or of any alias of entry `p` (its 1-based position in `terms`) is a span with the
  entry's type and `link=f"dictionary:{p}"`. An entry without aliases gives spans without a
  link (see "Decisions made while implementing").
- The construction validates the entries. Messages name positions and never terms or aliases:
  - an alias with an empty key: `term 3, alias 2: the alias is empty`;
  - an alias with the same key as its own term or as an earlier alias of the same entry:
    `term 3, alias 2 repeats the term` or `term 3, alias 2 repeats alias 1`;
  - more than 25 aliases: `term 3 has more than 25 aliases`;
  - one key (term or alias) in two entries: `terms 3 and 7 share a term or alias`. One case
    is kept from v0.1.0: an entry with the same term key and the same type as an earlier entry
    is ignored when neither of them has aliases. The same key with different types keeps its
    current message.
- The `_type_of` fallback for the letters that `re.IGNORECASE` and case folding treat
  differently (`İ`, `ı`) returns the entry (its type and its link). It searches aliases too.

### Configuration (`config.py`, `load_terms`)

```yaml
terms:
  - term: ACME Corporation
    type: ORG
    aliases: [ACME, ACME Corp.]
```

- `aliases` is optional and is a list of strings. `entry 4: 'aliases' must be a list of
  strings` when it is not. An empty alias: `entry 4, alias 2: the alias is empty`. Unknown keys
  are still rejected: `only 'term', 'type' and 'aliases' are allowed`.
- The loader checks the shape. The detector checks the relations between surfaces (repeats,
  shared keys, the limit), as it already does for duplicate terms.

### Documentation

- **Architecture.**
  - §2: a placeholder is `[TYPE_N]` or `[TYPE_N_V]`; new vocabulary entries **Link** and
    **Variant**.
  - §3.1: the `link` field of `Span`.
  - §3.3: the last sort key.
  - §4.2: the five `assign` rules.
  - §4.3 step 2: the link.
  - §5.1: the format with a variant.
  - §5.2: the new pattern, a table row for the variant forms (`[org_1 b]`, `[ORG\_1\_B]`), the
    groups 1–3 and 4–6, and the bounds.
  - §5.3: the round trip keeps each alias as written.
  - §10: a decision row. Decision: variant placeholders for linked surfaces. Rejected
    alternative: one placeholder per entity, restored as its first surface. Reason: each
    surface restores to itself, so the round trip stays lossless and tool arguments keep the
    user's wording.
- **Usage.**
  - §5: the `aliases` key, what the model sees, and the conflict errors.
  - §7: a bullet saying that aliases are linked and restored as written, that bracketed text
    such as `[Step 1 a]` becomes a `LITERAL`, and that a model may write the placeholder of
    another variant (`[ORG_1]` where the user wrote `ACME`). The user then reads that variant's
    surface.
  - §10: `x-aliasgate-entities` counts placeholders, variants included.
- **Threat model**, risk 7: the provider also sees which placeholders are written forms of one
  entity (`[ORG_1_B]` is another name of `[ORG_1]`). This is intended.
- **Example dictionary:** give `ACME Corporation` the alias `ACME`. If a test that uses the
  example files changes, the change must be exactly the alias's effect.
- **CHANGELOG:** `Unreleased` → `Added`.

## Tests

Use synthetic values only: `ACME Corporation` / `ACME`, `Globex S.L.` / `Globex`,
`Project Falcon` / `Falcon`.

`test_placeholders.py`
- `format_placeholder("ORG", 1, "B") == "[ORG_1_B]"`, and without a variant the output is
  unchanged.
- `format_placeholder` rejects `"A"`, `"b"`, `"BB"`, `"1"` and `""` as variants, and the
  message does not contain the value.
- The tolerant pattern fully matches `[ORG_1_B]`, `[org_1_b]`, `[ORG 1 B]`, `[ORG_1 B]`,
  `[ ORG_1_B ]`, `[ORG\_1\_B]`, `\[ORG_1_B\]` and `[CREDIT_CARD_3_Z]`.
- It finds nothing in `[ORG_1_]`, `[ORG_1B]`, `[ORG_1_BB]`, `[ORG_1__B]`, `[ORG_1_1]`,
  `[ORG_1-B]` and `[ORG_1_É]`.
- `parse_match` returns `("ORG", 1, "B")` for `[org_1 b]` and `("ORG", 1, None)` for `[ORG_1]`.
  The existing `parse_match` tests gain the third element. This is the listed interface change,
  not a weakened test.
- The longest constructible match, with a variant, is 101 characters, which is within
  `MAX_TOLERANT_LENGTH`. A two-letter variant is one step past the bound and is not a
  placeholder.
- property: `parse_match` inverts `format_placeholder` for every type, number and variant.
- property: no match is longer than `MAX_TOLERANT_LENGTH` (add optional variant parts to the
  generator).
- property: on random text, every match of the v0.1.0 pattern (keep a copy of it in the test
  file) is a match of the new pattern with the same offsets and no variant.

`test_types.py`
- `link` defaults to None, and `dictionary:7` is accepted.
- These links are rejected with `ValueError`, and the message does not contain the link:
  `"Dictionary:7"`, `"dictionary"`, `"dictionary:"`, `"dictionary:x"`, `"dictionary:7 "`,
  `"ACME Corporation"`, `""`.

`test_resolve.py`
- Two spans that differ only by link resolve to the same span whatever the input order.
- Extend the existing order-independence property with links.

`test_mapping.py`
- Linked surfaces share a number. The first is `[ORG_1]` with variant None, the second
  `[ORG_1_B]` with variant `"B"`, and the third `[ORG_1_C]`.
- The same link with another type starts its own group.
- A known key returns its entity even when another link is given, and it does not join that
  group.
- A link group and an unlinked entity of the same type are numbered in order of assignment
  (`[ORG_1]`, `[ORG_1_B]`, `[ORG_2]`, `[ORG_1_C]`).
- After 25 variants, the 27th distinct surface of a group gets the next number with no
  variant, and so does every later one.
- `lookup` with and without a variant; an unknown variant returns None.
- `repr` of the mapping and of an entity reveals no surface, key or link.
- Extend the existing uniqueness property with links: every placeholder is unique and resolves
  to its own surface.

`detectors/test_dictionary.py`
- An alias is detected with its entry's type and link, and the term and its aliases share the
  link.
- Two entries have different links.
- At one position the longest of the term and its aliases wins.
- An alias is matched case-insensitively, with flexible whitespace and as a whole term
  (`ACMEs` is not a match).
- Every error above is raised, and each message contains positions and no term or alias. Check
  this by searching the message for each surface of the test entries.
- A duplicate entry without aliases is still ignored, as in v0.1.0. A duplicate entry where
  either side has aliases is an error.
- 25 aliases are accepted and 26 are rejected.
- `repr(Term(...))` shows neither the term nor the aliases.

`test_config.py`
- `aliases` is read into `Term.aliases` as a tuple, and is `()` when absent.
- Errors for a non-list `aliases`, a non-string item, an empty alias and an unknown key.

`test_engine.py`. Use a dictionary entry `ACME Corporation` with the alias `ACME`, or the
fake detector with links.
- `["ACME Corporation signed.", "ACME paid."]` gives `["[ORG_1] signed.", "[ORG_1_B] paid."]`,
  and restoring gives back both inputs exactly.
- When the alias comes first, the alias is `[ORG_1]` and the term is `[ORG_1_B]`.
- A case variant of an alias (`Acme`) gets the alias's placeholder and restores as the alias's
  first form.
- Propagation gives a variant's placeholder to an occurrence the detector missed: the fake
  detector is case-sensitive and the text has a lower-case alias.
- Two entries give two families (`[ORG_1]`, `[ORG_1_B]`, `[ORG_2]`).
- An allow-listed alias stays in place while its term is replaced.
- An input `[ORG_1_B]` becomes `[LITERAL_1]` and is restored exactly.
- Without links, the existing tests pass unchanged. That is the compatibility check.
- property (round trip) with linked fake words and `propagate=False`: restoring gives back
  every input exactly.
- property (no residue) with linked fake words: no surface with a key of at least 3
  characters remains in the output.
- property (append stability) with linked fake words: for batches `A` and `A + B`, every
  entity of `mapping(A)` keeps its placeholder, variant and surface in `mapping(A + B)`.

`test_restore.py` and `test_stream.py`
- The variant forms are restored: `[org_1_b]`, `[ORG 1 B]`, `\[ORG_1_B\]`, and
  `[ORG_1_B]` inside JSON with `json_escape=True`.
- `[ORG_1_F]` (unknown variant of a known number) and `[ORG_1_A]` are left unchanged and
  counted as unresolved.
- Add variant placeholders to the generators of the chunking properties, with at least 200
  examples each. Run the any-chunking property once locally with 20,000 examples and record the
  result in the pull request, because architecture §5.2 forbids changing the pattern without
  re-running it.

`test_audit.py`
- A variant's surface present anywhere in the outbound body is reported as leaked, with its
  type.

`proxy/test_app.py`
- With a dictionary that has an alias, an OpenAI request containing the term and the alias
  reaches the mocked upstream as `[ORG_1]` and `[ORG_1_B]`. The response
  `"[ORG_1_B] and [ORG_1]"` reaches the client with both surfaces as written, and
  `x-aliasgate-entities` is `2`.

## Acceptance checklist

- [ ] Without aliases, every pre-existing test passes with unchanged expectations. The one
      exception is `parse_match`'s return value, which gains the variant; it is listed in the
      pull request.
- [ ] Round trip, no residue and append stability hold with linked spans, with at least 200
      Hypothesis examples each. The any-chunking stream property was run once with 20,000
      examples, and the pull request records it.
- [ ] A link can never carry text: `Span` validates its format, and a test proves it.
- [ ] Dictionary and configuration error messages contain positions, never terms or aliases.
- [ ] Architecture, usage, threat model, example dictionary and CHANGELOG are updated in the
      same pull request.
- [ ] Manual check, run only after the maintainer approves the spend (reuse existing resources,
      a hard cap in the harness). Send one request containing a term and an alias through
      `aliasgate serve` to a real model, and record whether the answer used `[ORG_1_B]` and how
      it was restored.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Linking `PERSON` or any other surfaces by tokens, by the ML model or by the provider's model
  (backlog §1).
- Links set by rules or by the ML detector.
- Telling the model what a variant letter means (backlog §15).
- Restoring an unknown variant, or a form without the letter, as anything other than what the
  mapping holds. An unknown variant stays unresolved (security invariant 6).
- Changes to the output of `aliasgate check` and `aliasgate demo`.

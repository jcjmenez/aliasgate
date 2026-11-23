# Slice 18: organisation mentions evaluation set

## Goal

The evaluation harness measures how the gateway treats the shorter forms of an organisation's
name, which no corpus contains today. A new synthetic corpus of organisation mentions in English
and Spanish holds full names with their legal form (`Brightwater Logistics Ltd`,
`Transportes Ventisquero S.L.`) together with the shorter forms people write: the name without
its legal form (`Brightwater Logistics`), one distinctive word (`Brightwater`, `Ventisquero`),
the same word in lower case. It also holds the cases where replacing or linking a word would go
wrong: two organisations that share a generic word (`Logistics`), and that generic word used on
its own (`Logistics costs rose`). The corpus labels which mentions name the same organisation.

The slice changes no detection and no placeholder. It records today's numbers as the baseline
that [slice 19](slice-19-org-forms.md) must improve. With a company dictionary, every shorter
form is sent to the provider unchanged, because neither the dictionary nor propagation finds it,
and the leak audit cannot see it either, since it is no entity's surface. In the existing
corpora, none of the 405 gold `ORG` spans is a shorter form of another one, so this gap has never
been measured.

This is the first of two slices for backlog §1, part 3 (linking organisation names). It reuses
the entity ids and link scores of [slice 15](slice-15-person-evaluation.md).

## Depends on

Slice 17 (released in v0.5.0). Every earlier slice is merged.

## Decisions taken with the maintainer before implementation (2026-11-23)

1. **Two slices, specified together.** This slice builds the evaluation set and records the
   baseline. Slice 19 builds the linking and gates it on this corpus. Rejected: one slice for
   both, which would be a large pull request with a new generator and engine changes together.
2. Decisions 2 to 4 concern the engine and are written in slice 19: which words of an
   organisation are shorter forms, the name without its legal form as a form of its own, and
   persons first for a word that a person and an organisation share.

## Found while specifying (prototype of 2026-11-23, outside the repository, synthetic data)

- **Dictionary.** With seven hand-written ORG terms and no aliases, every shorter form was
  forwarded unchanged: `Brightwater` and `Brightwater Logistics` after
  `Brightwater Logistics Ltd`, `Tobermory Freight` after `Tobermory Freight PLC`, `Ventisquero`
  after `Transportes Ventisquero S.L.`, `Albarrán y Ledesma` and `Albarrán` after
  `Construcciones Albarrán y Ledesma S.A.`, `Peñalver` after `Grupo Peñalver`, `Halvorsen` after
  `Halvorsen Marine Group`. Dictionary aliases (slice 13) cover a form only when the operator
  lists it.
- **ML layer** (`urchade/gliner_multi_pii-v1`, default settings). The model found most single
  distinctive words, but gave each a number of its own (`[ORG_2]` for `Brightwater` after
  `[ORG_1]`) or the type `PERSON` (`Lewis` after `Lewis Engineering Ltd`, `Albarrán`). It
  missed `Peñalver` in `En Peñalver necesitamos el informe` and `Ledesma` in
  `Albarrán y Ledesma quiere la factura`. In Spanish it often left the leading generic word out
  of the span (`Suministros [ORG_1]` for `Suministros Piñeiro S.A.`).
- **A prototype corpus** with the nine categories below, 20 examples each per language from one
  template per category, gave these baselines (leak rate): dictionary 0.2486 in English and
  0.2303 in Spanish, every shorter form leaked; ML layer 0.2039 and 0.1908, with 72 of 144 and
  50 of 127 same-organisation pairs linked, and no wrong pair. Slice 19 has the numbers per
  category, and those with the prototype engine.

## Dependencies to add

None. Faker is already a development dependency (slice 11).

## Files

| File | Action |
|---|---|
| `tools/generate_org_corpus.py` | new: the organisation corpus generator |
| `tests/eval/corpus/orgs.en.jsonl`, `tests/eval/corpus/orgs.es.jsonl` | new, generated |
| `tests/eval/corpus/orgs.terms.yaml` | new, generated: the full names, as a dictionary |
| `tests/eval/corpus/orgs.yaml` | new, hand-written: a configuration that loads that dictionary |
| `tests/eval/test_org_corpus.py` | new |
| `docs/evaluation.md` | change: the organisation corpus and its baseline |
| `CONTRIBUTING.md` | change: the organisation corpus command |
| `CHANGELOG.md` | change: an `Unreleased` → `Added` entry |

## Specification

Read [docs/evaluation.md](../evaluation.md), slice 15's "Person corpus" section and
`tools/generate_person_corpus.py` before starting. Nothing in `src/` changes, and neither do the
existing corpora or their numbers.

### Organisation corpus (`tools/generate_org_corpus.py`)

```bash
uv run python tools/generate_org_corpus.py           # write the files
uv run python tools/generate_org_corpus.py --check   # exit 1 if the committed files differ
```

It writes `tests/eval/corpus/orgs.en.jsonl`, `orgs.es.jsonl` and `orgs.terms.yaml`. It is
deterministic like the other two generators: Faker with locales `en_US` and `es_ES`, seeds 1801
and 1802, and a `random.Random` seeded the same way for every other choice. It shares no code
with the other generators. It imports `normalize_key` from `aliasgate.mapping` and
`COMMON_WORDS` from `aliasgate.common_words`. A later change to `COMMON_WORDS` that changes this
corpus makes `--check` fail, and the pull request that changes the list regenerates the corpus
and records the new numbers.

**Organisations.** Each organisation has one or more distinctive words, at most one generic
word and one legal form, in the order of its language:

| Shape | English | Spanish |
|---|---|---|
| standard | distinctive, generic, legal form: `Brightwater Logistics Ltd` | generic, distinctive, legal form: `Transportes Ventisquero S.L.` |
| several | three distinctive words, legal form `LLP`: `Hale, Mercer and Quill LLP` | two distinctive words: `Albarrán y Ledesma S.A.` |

- **Distinctive words** come from Faker's `last_name()`. A word is drawn again unless it has
  at least 3 characters, consists of letters only (`str.isalpha`), its key is not in
  `COMMON_WORDS`, and it is not one of the generator's generic words, legal forms or
  connectors (`and`, `y`, `e`, `of`, `the`, `de`, `del`, `la`, `las`, `los`, `el`). Each
  distinctive word is used by one organisation only, in both corpora together, except in
  `common` (below).
- **Generic words** are drawn from these lists. Slice 19's engine list must contain them all.
  - English: `Analytics`, `Capital`, `Consulting`, `Engineering`, `Freight`, `Holdings`,
    `Logistics`, `Marine`, `Partners`, `Software`, `Solutions`, `Systems`.
  - Spanish: `Consultoría`, `Construcciones`, `Distribuciones`, `Grupo`, `Ingeniería`,
    `Inversiones`, `Logística`, `Servicios`, `Soluciones`, `Suministros`, `Talleres`,
    `Transportes`.
- **Legal forms.** English: `Ltd`, `LLC`, `PLC`, `Inc.`, `Corp.` (and `LLP` for the shape with
  several words). Spanish: `S.L.`, `S.A.`, `S.L.U.`, `S.A.U.`, `S.Coop.`.
- **Spanish `y`.** In the shape with several words, the word after `y` is drawn again when it
  starts with `i`, `í`, `hi` or `hí` in any case, where Spanish writes `e`.
- **One example's organisations** share no word, except their generic word in `shared`. On a
  collision, draw again.
- **The bare name** of an organisation is its full name without the legal form:
  `Brightwater Logistics`, `Transportes Ventisquero`, `Hale, Mercer and Quill`.

**Gold spans.**

- Every mention of an organisation is an `ORG` span with the organisation's id: `o1`, `o2`, and
  so on, in order of first mention.
- A span covers the name as written: the full name with its legal form, final `.` included
  (`Transportes Ventisquero S.L.`), the bare name, or one distinctive word. Punctuation after
  the name and a possessive `'s` stay outside.
- A generic word used on its own (`Logistics costs rose`) is not a gold span.
- No template names a person. A full name never ends a sentence: it is followed by `,`, `:`,
  or a space and a lower-case letter (a verb, `and`, `y`), so that the final `.` of a legal form
  is never also the end of a sentence.

**Categories.** Every example id is `{lang}-{category}-{nnn}`, with `nnn` from `001` within its
category. Each category has two templates per language, and each template is rendered 10 times,
alternating (`001` is the first template, `002` the second). That gives 9 × 20 = 180 examples
per language. Write the templates by hand, in the voice of the existing corpora (tickets,
emails, CRM notes, chat messages), with one to four sentences, and vary them. The examples below
show what each category must contain.

| Category | What the template contains | English example | Spanish example |
|---|---|---|---|
| `full` | Two organisations, each named twice in full | `Brightwater Logistics Ltd and Corrigan Freight PLC signed the deal. Corrigan Freight PLC pays first, then Brightwater Logistics Ltd ships the goods.` | `Transportes Ventisquero S.L. y Construcciones Albarrán S.A. firmaron el acuerdo. Construcciones Albarrán S.A. paga primero y Transportes Ventisquero S.L. envía la mercancía.` |
| `bare` | A full name, later its bare name | `We signed with Brightwater Logistics Ltd last week. Brightwater Logistics will send the invoice on Monday.` | `Firmamos con Transportes Ventisquero S.L. la semana pasada. Transportes Ventisquero enviará la factura el lunes.` |
| `word` | A full name, later its distinctive word alone, twice | `Ticket from Brightwater Logistics Ltd: the parcel is late. Brightwater wants a refund, so please call Brightwater today.` | `Incidencia de Transportes Ventisquero S.L.: el paquete llega tarde. Ventisquero quiere el reembolso.` |
| `before` | The distinctive word alone, then the full name | `Brightwater called on Monday about the order. Later, Brightwater Logistics Ltd sent the signed form.` | `Ventisquero llamó el lunes por el pedido. Después, Transportes Ventisquero S.L. envió el formulario.` |
| `multi` | An organisation of the shape with several words, later two of its distinctive words alone, one of them not the first | `Hale, Mercer and Quill LLP sent the draft. Mercer asked for comments, and Quill will sign it.` | `Albarrán y Ledesma S.A. reclama el pago. Ledesma quiere la factura y Albarrán pide una copia.` |
| `shared` | Two organisations with the same generic word, later each distinctive word alone | `Welch Logistics LLC and Pitts Logistics Ltd both sent a bid. Welch offered a lower price than Pitts.` | `Transportes Villalba S.A. y Transportes Arregui S.L. enviaron una oferta. Villalba ofreció un precio menor que Arregui.` |
| `generic` | A full name, its generic word on its own and capitalised (a sentence start or a heading), not a gold span, and later the distinctive word alone | `Andrews Consulting Inc. sent the quarterly report. Consulting costs rose this quarter, and Andrews expects more.` | `Talleres Jódar S.Coop. envió el informe trimestral. Talleres y repuestos subieron este trimestre, y Jódar espera más.` |
| `lower` | A full name, later its distinctive word in lower case, as in a quick chat | `Brightwater Logistics Ltd confirmed the order. thanks, brightwater will call back tomorrow` | `Transportes Ventisquero S.L. confirmó el pedido. gracias, ventisquero llamará mañana` |
| `common` | An organisation whose distinctive word is a common word that is also a name, later that word alone, capitalised | `Stone Logistics Ltd shipped the parts. Stone says the order is complete.` | `Transportes Mesa S.L. envió las piezas. Mesa dice que el pedido está completo.` |

- In `common`, the distinctive word comes from these fixed lists, all in `COMMON_WORDS`.
  English: `Stone`, `King`, `Bell`, `Brown`, `Rice`, `Young`. Spanish: `Mesa`, `Plaza`, `Torre`,
  `Luna`, `Vega`, `Rubio`. A word may repeat across examples, but each full name is used once.
  Slice 19 leaves these words as written (its decision 2); the category measures that cost.
- In `generic`, the generic word on its own is the organisation's own generic word.

**`orgs.terms.yaml`.** A dictionary file in the format of `load_terms`: the first full-name
mention of every organisation of both corpora, as written, with type `ORG`. Each name appears
once, in order of first appearance, English first. In `before`, that is the full name after the
word. Bare names and words are not in it. The file starts with a comment saying it is generated
and synthetic.

**`orgs.yaml`.** Written by hand, not generated:

```yaml
# Configuration for the organisation corpus: the dictionary finds every full name, so shorter
# forms are measured on their own. Synthetic values only.
detection:
  dictionary:
    files: [orgs.terms.yaml]
```

### Baseline (measured in this slice, recorded in `docs/evaluation.md`)

Run both commands on the committed files and record every number of their output (leak rate,
`PERSON` and `ORG` rows, linking rows), per language:

```bash
uv run python -m aliasgate.eval --config tests/eval/corpus/orgs.yaml tests/eval/corpus/orgs.*.jsonl
ALIASGATE_DETECTION__ML__ENABLED=true uv run python -m aliasgate.eval --types PERSON,ORG tests/eval/corpus/orgs.*.jsonl
```

The second one needs `uv sync --extra ml` and runs locally only. Also record per category, for
the dictionary run, the `ORG` leaked characters and the wrong pairs, and for the ML run the
placed mentions, the leaked characters and the same, linked and wrong pairs. Compute them with a
throwaway script that groups examples by the category in their ids (`id.split("-", 1)[1]
.rsplit("-", 1)[0]`), and keep that script out of the repository. With the dictionary, expect no
leak in `full`, every shorter form leaked whole in the other categories, and in `generic` no
predicted span outside the gold spans. If a number contradicts this, report it in the pull
request before going on. Slice 19 needs the per-category numbers.

### Documentation

- `docs/evaluation.md`: a section "The organisation corpus", after "The person corpus": its
  categories, why CI uses the full-name dictionary, how to regenerate and check it, and the
  Faker version used; the baseline numbers under "Baseline".
- `CONTRIBUTING.md`: the command for the organisation corpus, next to the person corpus one.
- `CHANGELOG.md`: under `Unreleased`, `Added`: the organisation corpus.

## Tests

Synthetic values only.

`tests/eval/test_org_corpus.py`
- `--check` reports whether the files differ from a fresh generation, as the person generator
  test does it: generate into a temporary directory, check (0), change one file and check (1),
  delete it and check (1). That the committed files match the generator is checked by running
  `--check` with the Faker version in `uv.lock` before committing them.
- Each language has 180 examples. Ids follow `{lang}-{category}-{nnn}`, and each of the 9
  categories has 20 examples, alternating between two templates.
- Every example has at least one `ORG` span, every span is of type `ORG` and has an entity id,
  and ids are `o1`, `o2`, … in order of first mention.
- Every `ORG` span is the full name of its organisation, its bare name or one of its
  distinctive words; in `lower` the word is in lower case, and everywhere else every span starts
  with an upper-case letter. No span starts or ends with whitespace, and every full name is
  followed by `,`, `:`, or a space and a lower-case letter.
- Every distinctive word occurs only inside gold spans of its example. Outside `common`, no
  distinctive word's key is in `COMMON_WORDS`; in `common`, it is.
- Distinctive words are unique across both corpora outside `common`, and full names are unique
  everywhere. The organisations of one example share no word, except the generic word in
  `shared`, which they share.
- In `generic`, the organisation's generic word also occurs outside every gold span, capitalised.
- Every generic word of the generator is used, and every legal form.
- In Spanish `multi`, no word after `y` starts with `i`, `í`, `hi` or `hí`. Force the redraw in
  a unit test, as slice 15 did for its collision rules.
- The rules detect nothing: with the default configuration and no dictionary, `analyze`
  returns no span for any example.
- `orgs.terms.yaml` loads with `load_terms`, holds only `ORG` terms, and is exactly the first
  full-name mention of every organisation of both corpora, in order.
- With `orgs.yaml`, the harness reports no round-trip failure in either language, and every
  `ORG` span of `full` is fully covered.

## Acceptance checklist

- [ ] The existing corpora, their numbers, the CI recall gate and the person gate are unchanged.
- [ ] The organisation corpus has 9 categories × 20 examples per language and is reproducible
      with `--check`.
- [ ] The baseline is recorded in `docs/evaluation.md` for the dictionary run and the ML run,
      with the per-category numbers in the pull request and in this file ("Decisions made while
      implementing").
- [ ] `CHANGELOG.md`, `CONTRIBUTING.md` and `docs/evaluation.md` are updated.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Any change to detection, linking, placeholders or the engine (slice 19).
- Acronyms (`GHS` for `Global Harbour Shipping`), hyphenated organisation names
  (`Yang-Clark`), brand nicknames, organisations named after a person next to that person (the
  person corpus's `org` category already covers them).
- Examples made of several texts (conversation turns). Linking across texts is tested with unit
  tests in slice 19.
- A CI gate on the organisation corpus (slice 19).

# Slice 23: role words

## Goal

The message roles of the supported APIs (`system`, `developer`, `user`, `assistant`, `tool`,
`function`) are never replaced on their own. This closes the part of backlog §17 that rejects
the most requests.

Today, with the ML layer on, a tool described as `Creates a user.` is rejected with 500
`leak_audit_failed` on both routes. The model takes `user` for a person, the description
becomes `Creates a [PERSON_1].`, and the leak audit finds `user` in `"role": "user"`, which is
never a slot. Nothing leaks, but the request cannot be sent. The same happens without the ML
layer when a detected name holds a role word: a dictionary organisation `User Group Ltd` makes
`user` one of its variants, propagation replaces `tell the user`, and the audit finds `user` in
the role again. Measured below.

After this slice a contextual detector (the ML layer) never reports a role word on its own, and
name linking treats role words as common words. The audit, the ML threshold, the dialects and
the configuration do not change.

## Depends on

Slice 22 (v0.8.0 released). Every earlier slice is merged.

## Decisions taken with the maintainer before implementation (2026-11-27)

1. **Spans and forms.** A span of a contextual detector whose key is a role word is dropped,
   as an allow-listed span is (backlog §17, option B). The role words also join the common word
   list of slice 17, so a role word never stands alone for an organisation, and stands for a
   person only where it starts with an upper-case letter. Rejected: dropping spans only, which
   still rejected 9 of 16 requests naming an organisation or person whose name holds a role word
   with the ML layer, and 5 of 6 with dictionary terms and the ML layer off.
2. **The six role values of the two APIs, and nothing else.** `system`, `developer`, `user`,
   `assistant`, `tool` and `function`, compared by key, so `User` and `USER` count too, because
   the audit ignores case. They are every value of OpenAI's `role` and include Anthropic's two.
   No one is called by one of them, and none of the 5061 gold spans of the evaluation corpora is
   one. Rejected: adding the Spanish nouns (`usuario`, `asistente`, `sistema`, `herramienta`,
   `función`, `desarrollador`). They cause no rejection, because roles are English, and they
   are not protocol words. The ML layer still replaces `usuario` (in 14 of 40 Spanish texts),
   which costs utility only.
3. **Parameter names and model names stay in backlog §17.** With the ML layer on, 19 of 20
   English parameter names whose description is `<Name> name.` are rejected: the model takes
   `Customer name` for an entity, name linking makes the argument key `customer` a variant of it,
   and the schema's property name and `required` entry keep `customer`. A list of the backlog's
   nine role nouns cleared exactly those nine, and the other ten still failed. The model's name
   in the text (`You are Claude` with a `claude-*` model, `GPT` with `gpt-4.1-mini`) still
   rejects the request too. The allow list clears both (measured), and the usage guide says so.

## Decisions made while writing this spec

These follow from the decisions above or from the existing contracts. They are binding.

- **One constant, in a core module.** `ROLE_WORDS` lives in `common_words.py`, and
  `COMMON_WORDS` includes it. The engine drops spans by `ROLE_WORDS`; name linking needs no
  change of its own, since it already reads `COMMON_WORDS` for persons (`engine.py`) and for
  organisations (`orgs.py`). Not configurable: the allow list remains the operator's tool.
- **Contextual detectors only.** The literal, dictionary and rule layers are not filtered. The
  rules never return a role word, and an operator who lists `Assistant` as a dictionary term
  gets it replaced, and the request rejected by the audit, as today. The usage guide says so.
- **The whole key only.** `the user`, `current user` and `User Group Ltd` are kept and replaced
  as today. The audit matches whole terms, so it does not find `the user` in `"role": "user"`
  and does not reject them.
- **Before overlap resolution.** A dropped span never hides another span, as with the allow
  list. A role word inside a longer span of another detector stays part of that span.
- **Capitalised person forms are safe.** A role word that is a form of a person (`Tool` of
  `Ana Tool`) becomes a capitalised entity. The audit finds a capitalised entity only where a
  match starts with an upper-case letter (slice 17), and role values are lower case.
- **The output of a contextual detector changes, and only there.** Architecture §4.1 says that
  with no labels a contextual detector gives the output of an ordinary one. That sentence gets
  the exception of role words. No public signature changes.
- **No metric.** Allow-listed spans are not counted either. A count of dropped spans would say
  how often the model sees a role word, which is not a measure of the gateway.

## Found while specifying (prototype of 2026-11-27, outside the repository, synthetic data)

Reproduction through the gateway, in process, with a mocked upstream and the default ML model
(`urchade/gliner_multi_pii-v1` at its pinned revision, threshold 0.4, offline). Each request
had a system prompt, a user turn, an assistant turn with a tool call, a tool result and a last
user turn, and one tool `lookup_order`. The model was `gpt-4.1-mini` on the OpenAI route and
`claude-sonnet-5-5` on the Anthropic route. The prototype dropped contextual spans by key and
added the six words to `COMMON_WORDS` (an 8-line change).

| Case | v0.8.0 | Prototype |
|---|---|---|
| 80 short texts using role words (40 English, 40 Spanish), each as the tool description, and as the system prompt and the last user message: 320 requests | 92 rejected (88 English, 4 Spanish) | 4 rejected, all by the model's name |
| the same request with neutral texts | forwarded | forwarded |
| 8 texts naming `User Group Ltd`, `System Integrators Inc.`, `Tool Rental Ltd`, `Function Health Ltd`, `User Experience team` and others, as the system prompt: 16 requests | 9 rejected with the span drop alone | forwarded |
| ML off, dictionary `User Group Ltd`, `System Integrators Inc.` (ORG), `Ana Tool` (PERSON), the text using the word in lower case: 6 requests | 5 rejected | forwarded |
| 20 parameter names with `<Name> name.` (English) or `Nombre del …` (Spanish), call in the history: 80 requests | 38 rejected, all English | 38 rejected (decision 3) |
| the same with `allow_list: [customer, status]` | | `customer` and `status` forwarded |
| `You are Claude, …` / `You are built on GPT …` | rejected | rejected; forwarded with `Claude` / `GPT` allow-listed |

Where the audit found the value under v0.8.0: `messages[].role` in 88 (`user` in 72,
`assistant` in 20, both in 4, counting `User:` and `Assistant:` at the start of a text), `model`
in 4. The 4 Spanish rejections came from English words in Spanish text (`el assistant`,
`el user`). On its own, the model took `user` in 17 of the 40 English texts, `assistant` in 4
and `developer` in 1, and never `system`, `tool` or `function`. The organisation texts were
measured with the span drop alone, not under v0.8.0. With the dictionary, the Anthropic
request naming `Ana Tool` was forwarded under v0.8.0 too, because Anthropic has no `tool` role
and the audit does not match `tool` inside `tool_use`. The other five were found in a role, or
in Anthropic's top-level `system` key. With the ML layer, `Function Health Ltd` was also found
in OpenAI's `"type": "function"`.

**Evaluation.** With the prototype, the six evaluation reports are identical to v0.8.0's:
rules only on the synthetic corpus, the person and organisation corpora with their
configurations, and the same three with the ML layer on. No gold span is a role word.

**Test changes.** With the prototype applied on its own, 1 of the 2554 tests changed: the size
of `COMMON_WORDS` (633 to 639). It is listed under Tests.

## Dependencies to add

None.

## Files

| File | Action |
|---|---|
| `src/aliasgate/common_words.py` | change: `ROLE_WORDS`, part of `COMMON_WORDS` |
| `src/aliasgate/engine.py` | change: drop contextual spans whose key is a role word |
| `tests/test_common_words.py`, `tests/test_engine.py` | change |
| `tests/proxy/test_app.py`, `tests/proxy/test_app_anthropic.py` | change |
| `tests/detectors/test_gliner_real.py` | change: one `ml` test |
| `docs/architecture.md` | change: §3.2, §4.1, §4.3, §10 |
| `docs/usage.md` | change: §6.2, §6.3, the `leak_audit_failed` row |
| `docs/threat-model.md` | change: residual risk 1 |
| `docs/plan/slice-17-person-forms.md` | change: a "Changed in slice 23" note |
| `docs/plan/README.md`, `docs/plan/backlog.md`, `README.md` | change: status, backlog §17 |
| `CHANGELOG.md` | change: `Unreleased` entries |

The audit, the detectors, the dialects, the proxy, the mapping, the configuration and the
evaluation corpora do not change.

## Specification

Read slices 09, 16, 17, 19 and 21, and architecture §3.2, §4.1 and §4.3, before starting.

### Role words (`common_words.py`)

```python
ROLE_WORDS: Final[frozenset[str]]
"""The message roles of the supported APIs: a contextual detector never reports one on its
own, and as a common word one never stands alone for a name in lower case."""
```

It holds exactly `system`, `developer`, `user`, `assistant`, `tool` and `function`, each
written as `normalize_key` gives it. `COMMON_WORDS` is the union of `ROLE_WORDS` and the four
groups of slice 17: 639 keys. Its docstring says that it includes the role words. The module
docstring names both constants.

### Engine (`engine.py`)

In `Pseudonymizer._detect`, the spans of each detector in `contextual` whose key
(`normalize_key` of the span's surface) is in `ROLE_WORDS` are dropped before the spans of
that text go to overlap resolution, as allow-listed spans are. The spans of the literal detector
and of `detectors` are not filtered by role words. `analyze` and `pseudonymize` share this step,
so they agree. The class docstring says that a contextual detector never reports a role word on
its own.

Nothing else changes in the engine: name linking reads `COMMON_WORDS`, which now holds the role
words. So does `orgs._kind`, which classifies a role word as a common word.

### Architecture (`docs/architecture.md`)

- §3.2, after the allow-list paragraph: a span of a contextual detector whose key is a role
  word (`ROLE_WORDS`: `system`, `developer`, `user`, `assistant`, `tool`, `function`) is
  dropped too. The reason: the model takes them for names, and the audit then finds them in the
  message roles, which are never slots, and rejects the request.
- §4.1: the sentence "With no labels, or no contextual detector, the output is the one the
  engine gave before labels existed" gets the exception of role words.
- §4.3, step 1: "drop allow-listed spans and the role-word spans of contextual detectors". Where
  the step names `COMMON_WORDS`, say that it includes the role words.
- §10, a new row. Decision: contextual detectors never report a role word on its own, and role
  words are common words (slice 23). Rejected: the audit skipping protocol words, dropping
  spans only, or dropping every string the request forwards unchanged. Reason: the audit is the
  last check and stays exact; dropping spans only leaves names holding a role word; dropping
  the model, tool and property names would forward a name found only by the ML layer that is
  also such a name (backlog §17, option C).

### Documentation

- `docs/usage.md`, §6.2, the `threshold` bullet: an extra detection costs utility, except when
  it equals a string the gateway forwards unchanged. Then the leak audit rejects the request
  with `leak_audit_failed`. Role words are never reported on their own since this slice. Name
  what can still cause it (the model's name in the text, a tool name, a schema property name
  that the model or name linking takes for a name, as with `Customer name.`, and a dictionary
  term that is a role word, which is replaced like any other term), and the
  workaround: put the word on the allow list (`customer`, `GPT`), which also stops it being
  detected anywhere else. Allow-listing a first name (`Claude`) keeps full names
  (`Claude Martin`) detected but forwards the first name on its own.
- `docs/usage.md`, §6.3: the list has 639 words and holds the message roles too, so a role word
  that is a name word (`Tool` of `Ana Tool`) is replaced only where capitalised, and a role word
  never stands alone for an organisation (`User` of `User Group Ltd`).
- `docs/usage.md`, the `leak_audit_failed` row of the error table: add the ML case (a model
  false positive equal to the model's name or a schema property name) to the typical causes,
  and the allow list to the fixes.
- `docs/threat-model.md`, residual risk 1: role words join the common words that are sent as
  written in lower case (`tool` of `Ana Tool`), and a role word joins the distinctive words that
  never stand alone for an organisation (`User` of `User Group Ltd`).
- `docs/plan/slice-17-person-forms.md`: a "Changed in slice 23" note where the list's 633 keys
  are counted: the six role words join it, 639 keys.
- `CHANGELOG.md`, under `Unreleased`:
  - `Fixed`: with the ML layer on, a request was rejected with `leak_audit_failed` when the
    model took a message role (`user`, `assistant`) in the text for a name, as in a tool
    described as `Creates a user.`; and a detected name holding a role word (`User Group Ltd`,
    `Ana Tool`) made every request using that word in lower case rejected, with or without the
    ML layer.
  - `Changed`: the six message roles are common words, so a role word stands for a person only
    where capitalised and never for an organisation on its own.
- Backlog §17: the roles part is cut into this slice. Still open: the model's name, tool names
  and parameter names, with the measurement of decision 3, and the allow list as the
  workaround.

## Tests

Synthetic values only: `Ana Ruiz`, `Ana Tool`, `User Group Ltd`, `Juan Pérez` and
`ACME Corporation` from the proxy dictionary, and plain words. Write non-ASCII characters with
`\u` escapes.

### Changed expectation (decision 1; the pull request names it)

`test_common_words.py`

- `test_the_list_has_the_633_keys_of_the_slice_file` becomes
  `test_the_list_has_the_633_keys_of_slice_17_and_the_6_role_words`: 639 keys.

### New tests

`test_common_words.py`

- `ROLE_WORDS` is exactly the six words, each its own `normalize_key`, and a subset of
  `COMMON_WORDS`.

`test_engine.py` (use `WordListDetector` from `tests/fakes.py` as the contextual detector)

- For each of the six words, written in lower case, capitalised and upper case: a contextual
  detector's span of exactly that word is not replaced, and `analyze` reports no span for it.
- The same word found by a detector in `detectors` (not contextual) is replaced.
- A contextual span that holds a role word but is longer (`the user`, `User Group Ltd`) is
  replaced.
- A dropped span hides nothing. In `Ask the user Ana Ruiz.`, one contextual detector reports
  `user` with score 0.95 and another reports `user Ana Ruiz` with score 0.9: the output is
  `Ask the [PERSON_1].`
- With `person_tokens`, a dictionary person `Ana Tool`: `Tool` capitalised elsewhere is replaced
  as the person's variant, and `tool` in lower case is not.
- With `org_tokens`, a dictionary organisation `User Group Ltd`: neither `User` nor `user` on its
  own is replaced; `User Group` is, as the bare name.
- property: for any list of words mixing role words (in any case) and other words, a contextual
  detector that reports every word as a PERSON leaves every word whose key is a role word as
  written and replaces every other word.

Proxy tests (`test_app.py`, `test_app_anthropic.py`)

- With a contextual detector that finds `user`: a request whose tool is described as
  `Creates a user.` answers 200, and the upstream gets the description unchanged. With the same
  detector passed as an ordinary one, the request answers 500 `leak_audit_failed`, which shows
  why the drop is limited to contextual detectors. Both routes.
- With a dictionary organisation `User Group Ltd` and the text
  `Escalate to User Group Ltd, then tell the user.`, the request answers 200 and the upstream
  gets `Escalate to [ORG_1], then tell the user.` Both routes.

`test_gliner_real.py` (marked `ml`)

- With the real model and the default settings, `Creates a user.`,
  `Let the assistant summarise the ticket.` and `Reply to the user politely.` go through
  `build_pseudonymizer` unchanged. Under v0.8.0 each one lost its role word to `[PERSON_1]`.

Evaluation: every gate in `tests/eval` passes unchanged. Record in the pull request that the
evaluation reports, with and without the ML layer, are the same as before.

Mutation: in turn, drop the filter, apply it to every detector, compare surfaces without
`normalize_key`, filter after overlap resolution, drop spans that contain a role word instead of
spans that are one, and leave the role words out of `COMMON_WORDS`. Confirm that a test fails
each time.

## Acceptance checklist

- [ ] Every row of the reproduction table behaves as the prototype column says, on both routes,
      with the real model (record the counts in the pull request).
- [ ] A contextual span whose key is a role word is dropped before overlap resolution; spans of
      other detectors and longer spans are kept.
- [ ] `ROLE_WORDS` has the six words and `COMMON_WORDS` the 639 keys.
- [ ] Every pre-existing test passes unchanged, except the expectation listed under Tests.
- [ ] The evaluation reports are unchanged, with and without the ML layer.
- [ ] The mutation checks are done and recorded in the pull request.
- [ ] Architecture, usage, threat model, slice 17, backlog §17 and CHANGELOG are updated in the
      same pull request.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- The model's name, tool names and parameter names that the model or name linking takes for
  names (decision 3; backlog §17).
- Spanish role nouns, and other words the model over-predicts (`you`, `the user`).
- Renaming schema property names and `required` entries (slice 21, out of scope).
- Changes to the audit, the ML threshold, the detectors, the dialects, the proxy, the mapping,
  the configuration or the evaluation corpora.

# Backlog (after v0.1.0)

Not yet cut into slices. Each item needs its own slice file, written to the format in
[README.md](README.md), before anyone implements it. Ordered by expected value.

## 1. Entity linking (names of people)

**Problem.** "Juan Pérez", "Mr. Pérez" and "Juan" get three placeholders, so the model treats
them as three people.

**Done so far.** Dictionary aliases are cut into
[slice 13](slice-13-dictionary-aliases.md). That slice adds `Span.link`, link groups in the
mapping and variant placeholders: `[ORG_1]` for the first surface seen and `[ORG_1_B]` for the
next. Restoration stays lossless.

**Direction.** Token-subset linking for `PERSON` on top of slice 13. A single token that equals
the first or last token of exactly one person seen earlier in the batch becomes a variant of
that person. Only earlier persons count, so append stability holds. The linking runs in the
engine, not in a detector, because it needs the persons of the whole batch. It needs an
evaluation set for wrong merges, which are worse than missed merges. Specify it once slice 13 is
merged and measured, including whether models keep the variant letters.

**Cut into [slice 15](slice-15-person-evaluation.md) and [slice 16](slice-16-person-tokens.md).**
Slice 13's manual check showed that models keep the variant letters. A prototype then found a
leak: with the full name detected, the shorter forms of a name (`Ms. Johnson`, `Alice`,
`Sr. Pérez`) are forwarded unchanged, because no detector finds them and propagation looks only
for whole surfaces. Slice 15 adds a person corpus with gold entity ids and link scores. Slice 16
replaces and links the shorter forms where they are capitalised. A form is one name word in any
position, or a contiguous run of words. Nicknames, lower-case forms and other types stay here.

**Still open after slice 16.** Lower-case shorter forms (`thanks alice`), nicknames (`Bob` for
`Robert`), diminutives (`Juanito`), transliterations, a part of a hyphenated name (`José` for
`María-José`), the words of a detected name with only one name token (`Dr. Smith`), the
English first name `Don` (read as a title), and linking the words of other types, such as
`ORG`.

**Cut into [slice 17](slice-17-person-forms.md).** Lower-case shorter forms are replaced unless
the word is on a fixed list of English and Spanish common words, which keep the capitalised
rule. A detected name with one name word and a title or an initial counts as a person, each part
of a hyphenated name word is a shorter form of its own, and `Don` is a first name when the rest
of the name has one name word. A prototype measured the leak rate of the person corpus falling
from 0.0425 to 0.0303 in English and from 0.0339 to 0.0225 in Spanish, with the synthetic corpora
unchanged. Still open after it: nicknames, diminutives, transliterations, common words that are
also names written in lower case, and linking the words of other types.

**Cut into [slice 18](slice-18-org-evaluation.md) and [slice 19](slice-19-org-forms.md).**
Organisation names. With a company dictionary, the shorter forms of a term without aliases
(`Brightwater` and `Brightwater Logistics` after `Brightwater Logistics Ltd`) are forwarded
unchanged, and the leak audit cannot see them. The ML model finds most single words, but gives
them a number of their own or the type `PERSON`. Slice 18 adds an organisation corpus with gold
entity ids. Slice 19 replaces and links the distinctive words of a detected organisation and its
name without the legal form; legal forms, connectors, generic words (a fixed list) and common
words never stand for it alone, and a word shared with a person goes to the person. A prototype
measured the leak rate of a corpus like slice 18's falling from 0.2486 to 0.0146 in English and
from 0.2303 to 0.0138 in Spanish with a dictionary, and from 0.2039 to 0.0733 and from 0.1908 to
0.0507 with the ML layer, with no wrong merge and the person and synthetic corpora unchanged.
Still open after them: nicknames, diminutives, transliterations, common words that are also
names written in lower case, acronyms, and the words of other types (`ADDRESS`, `PROJECT`).

## 2. Realistic surrogates, opt-in per entity type

**Problem.** Placeholders reduce answer quality on some tasks (state of the art §5).

**Direction.** A surrogate generator behind the same mapping interface: format-preserving fake
emails, phones and identifiers first, where exact reversal is reliable because the surrogate is
a unique string. Names last, because models inflect and abbreviate them. Requires a collision
check (the surrogate must not occur in the request) and a restoration audit.

## 3. Stateful APIs and an encrypted session vault

**Problem.** The OpenAI Responses API with `previous_response_id`, conversation objects and
similar features keep history on the provider side, so the gateway cannot rebuild the mapping
from the request.

**Direction.** A pluggable `MappingStore` keyed by conversation id, with an in-memory
implementation and an encrypted one (envelope encryption, short TTL, keys from a KMS). This
changes the threat model (something reversible at rest) and must be opt-in. Placeholder ids
derived with HMAC become useful here.

## 4. More endpoints and dialects

OpenAI Responses API (stateless mode first), Google Gemini, AWS Bedrock and Vertex variants of
the Anthropic API, embeddings (one-way: pseudonymise only, with a warning about retrieval
quality).

## 5. Utility evaluation

Measure how much answers degrade: a fixed task set (summarise, extract, draft a reply, answer
questions about a document) run with and without the gateway against a real model, scored with
task-specific checks. Needs a budget and a policy for calling external models from CI (probably
manual runs only).

## 6. Documents and images

Text extraction for PDFs and office files in requests, OCR for images, then the normal engine.
Reconstruction of a file with placeholders is hard; a first version can convert attachments to
text parts. Until then they are rejected.

## 7. Alternative detector backends

OpenAI Privacy Filter, Presidio (for its recognisers in many languages), a local small LLM as a
second-opinion detector for high-sensitivity deployments. Each is a `Detector`. Compare them
with the evaluation harness before recommending any.

Found in slice 09: GLiNER scores spans of at most `max_width` words (12 for the default
model), so a longer address can only be detected in part. GLiNER2 models
(`fastino/gliner2-privacy-filter-PII-multi`) need the separate `gliner2` package. The default
model's loader also fetches `microsoft/mdeberta-v3-base`'s tokenizer unpinned; a backend that
loads every file from one pinned revision would close that gap.

## 8. Per-tool restore policy

Let the operator list tools whose arguments must not receive restored values (for example a URL
fetcher), to limit the exfiltration path described in the threat model, risk 4.

## 9. More rule coverage

National identifiers and tax numbers of other countries, passport numbers, licence plates,
national phone formats, more secret formats. One rule, its checksum and its vectors per change.

Lower-case IBANs were fixed in slice 11. [Slice 14](slice-14-rule-gaps.md) fixed the gaps
listed here until then: Spanish identifiers with a separator or dots, emails with non-ASCII
letters or an apostrophe, bare SSNs after a keyword, PEM keys without END line, the SSN replaced
as `PHONE`, and with `URL` enabled, an email or a token inside a URL. Still open, each a value
forwarded unchanged:

- A US SSN without hyphens and without a keyword right before it, for example in a table column
  whose header row says `SSN`. Nine bare digits are too ambiguous on their own; this needs
  column-aware detection.
- A PEM private key whose lines were joined with spaces and that has no END line: only its
  BEGIN line is replaced.
- With `URL` enabled, a dictionary term inside a URL beats the URL, because dictionaries rank
  above rules, so the rest of that URL is forwarded. Found in slice 14.
- A value that only partly overlaps a URL (a card number that starts at the end of a URL path
  and goes on after a space) wins, and the rest of the URL is forwarded. Replacing both needs
  the resolver to merge overlapping spans, which architecture §3.3 does not do. Found in slice
  14.

## 10. Request fields forwarded unchanged

Slice 05 leaves some OpenAI request fields that can carry end-user data untouched, because
changing them alters behaviour: `prompt_cache_key` (provider cache routing; OpenAI suggests it
in place of `user`), `web_search_options.user_location` (localised search), `messages[].name`,
and the keys of objects inside tool-call arguments. The leak audit (slice 10) still catches a
surface detected elsewhere in the request if it appears in a value of these fields. Direction:
per-field policy options (strip, pseudonymise, forward), defaulting to the current behaviour,
with the same review for the Anthropic dialect.

**Cut into [slice 20](slice-20-request-fields.md).** A reproduction through the gateway found
each of these fields, and a few more, forwarded unchanged when a value appears only there, and
the whole request rejected by the leak audit when a value detected elsewhere appears there too
(a stop sequence naming a person in the conversation). Slice 20 pseudonymises OpenAI
`messages[].name`, `metadata` values, `prompt_cache_key` and `stop`, Anthropic
`stop_sequences` (restoring the `stop_sequence` the answer repeats), output schema descriptions
and Anthropic tool `input_examples`, and removes `user_location` by default
(`policy.strip_user_location`). Still open after it: `enum`, `const`, `default`, `examples` and
`title` values in tool and output schemas (exact values for strict decoding, and two values
with the same key would become equal placeholders), object keys (metadata keys, the keys of
tool-call arguments), and an option to forward a field unchanged.

**Keys cut into [slice 21](slice-21-object-keys.md), schema values into
[slice 22](slice-22-schema-values.md).** A reproduction found that the leak audit did not check
keys. A value detected in the conversation left the gateway unchanged as an OpenAI `metadata`
key, an Anthropic `tool_use` input key or an `input_examples` key. Slice 21 audits every key and
pseudonymises the keys of data objects (tool-call arguments, tool inputs, input examples,
metadata) as label slots, which the ML layer does not read because it took 22 of 59 common
field names for entities. Slice 22 pseudonymises `enum`, `const`, `default` and `examples` as
label slots, and `title` and `$comment` as text. Still open after both: an option to forward a
field unchanged. Slice 21 is done as described here.

**Structured output answers are restored without JSON escaping** (found while specifying slice
22). An OpenAI `message.content` under a `json_schema` `response_format`, or an Anthropic text
block under `output_config`, is JSON text. Plain `restore` inserts a surface holding `"` or `\`
unescaped (`Ana "La Jefa" Ruiz`), so the client gets invalid JSON. Nothing leaks. The dialects'
`restore_response` does not know the request's output format, so a fix changes that contract;
the streamed path needs the same care.

## 11. Operations

OpenTelemetry tracing (attributes limited to counts and types), Helm chart, hot reload of
dictionaries, ONNX or quantised model for faster CPU inference, batching of inference across
concurrent requests. The ML detector runs one inference at a time per
process; the windows of one text could also be batched into one `inference` call (measured in
slice 09: 2.6 s for 3,000 words, one window after another).

## 12. Adapters

A LiteLLM guardrail and a LangChain or LlamaIndex wrapper built on the core library, for teams
that already run those.

## 13. Public evaluation datasets

Review licences and content of public PII datasets and add loaders for the acceptable ones, to
publish numbers comparable with other tools.

## 14. Byte-stable history for signed reasoning

Anthropic models reject a replayed `thinking` block when anything before it changed (preserved
thinking). Slice 08 found three ways the gateway can change an earlier turn on resend:
propagation revealing an entity an earlier text contained undetected, a placeholder variant
written by the model (`[person_1]`) coming back as `[PERSON_1]`, and an unresolved placeholder
becoming `[LITERAL_n]`. Options: restore variants and unresolved placeholders so that they
re-pseudonymise to the same text, and an option to stop propagation into earlier turns.
Measure first with a replay of real conversations.

## 15. Telling the model about placeholders

Models sometimes refuse a task because they see placeholders and take them for an unfilled
template (seen in the slice 08 manual check). An opt-in system note, added by the dialect, would
say that bracketed placeholders stand for real values the model must use as given. Measure its
effect with the utility evaluation (section 5) before turning it on by default.

## 16. Fold-aware propagation

Found in slice 10. Propagation finds other occurrences with a case-insensitive pattern, while
keys use NFKC and full case folding. Variants that share an entity's key but not its pattern
are not replaced: `STRASSE` for `Straße`, a decomposed `é`, full-width letters. The leak audit
catches them, so such a request is rejected rather than leaked. Replacing them instead needs
matching on normalised, case-folded text and mapping the offsets back to the original. NFKC
composes across characters, so this cannot be done one character at a time. The audit also
cannot see a value that is JSON-escaped inside a string (`P\u00e9rez` in a tool result that
is itself a JSON document).

## 17. ML false positives on fields forwarded unchanged

**Problem** (found while implementing slice 22). With the ML layer on, a tool described as
`Creates a user.` is rejected with 500 `leak_audit_failed` on both routes. The model takes
`user` for a person, the description becomes `Creates a [PERSON_1].`, and the leak audit finds
`user` in `"role": "user"`, which is never a slot. Nothing leaks, but the request cannot be
sent. Any false positive whose key equals a string the gateway forwards unchanged does the
same: a message role, `model`, a tool name, a schema property name or a `required` entry.
[Usage §6.2](../usage.md#62-the-ml-detector) says that an extra detection only costs utility;
here it costs the request.

**Measured** (2026-11-26, synthetic texts, the default model at its pinned revision, threshold
0.4, through the gateway with a mocked upstream; the scripts lived outside the repository):

- 80 short texts, 40 in English and 40 in Spanish, using user/usuario, assistant/asistente,
  system/sistema, tool/herramienta, function/función, model/modelo and developer. Each was sent
  as a tool description, or as the system prompt and as the last user message, in a request
  with system, user, assistant (with a tool call) and tool turns, on both routes: 240 requests.
  The same request with neutral texts is forwarded. 68 were rejected: 54 of 120 in English, 14
  of 120 in Spanish. The audit found the value in `messages[].role` in 62 (`user` in 36,
  `assistant` in 26), in `model` in 4 (`Claude` in `claude-sonnet-5-5` and `GPT` in
  `gpt-4.1-mini`, both taken for persons) and in the tool name in 2 (`create_user`, named in a
  Spanish description and taken for a person). The model took `usuario` and `asistente` for
  persons in most Spanish texts, which is harmless because roles are English; the Spanish
  rejections came from English words in Spanish text (`un user`, `el assistant`), `GPT` and
  `create_user`. It never took `system`, `tool` or `function` alone, and took `developer`
  twice, with no developer message in the request. The scores of the `user` and `assistant`
  spans were 0.48 to 0.92, so no threshold that keeps recall separates them.
- 20 common parameter names (`customer`, `owner`, `plan`, `team`, ...), each with a short
  English or Spanish description (`Customer name.`, `Nombre del cliente.`), as the one
  parameter of a tool whose call is in the history, on both routes: 80 requests. 22 were
  rejected, all English: 11 of 20 names on both routes (`customer`, `owner`, `plan`, `team`,
  `manager`, `contact`, `project`, `vendor`, `recipient`, `department`, `client`). The model
  takes `Customer name` for a person, name linking makes the argument key `customer` (a label
  slot) a variant of it, and the audit finds `customer` in the schema's property name and in
  `required`. When no slot held the name on its own (the call in the history used another
  argument name), 2 of 20 were rejected (`manager` and `recipient`, which the model took
  alone). None of these words is on the common or generic
  word lists of name linking.

**Options.**

- **A. The audit skips protocol words.** A string that is exactly a role, a content or schema
  type, or a key of the request shapes, compared case-sensitively, is not searched; or, per
  path, the closed values of `role` and `type`, which each dialect would have to declare,
  because the audit is a core module. Simulated: 68 rejections become 6. The false positive
  still reaches the provider (`Creates a [PERSON_1].`), and propagation still replaces the word
  in every text of the request (`Ask the [PERSON_1] politely.`). It changes the last check
  before forwarding, and the word list must cover every key of both APIs (Anthropic's top-level
  `system` included).
- **B. Contextual detectors never report a protocol word.** The engine drops a span of a
  contextual detector whose key is `system`, `developer`, `user`, `assistant`, `tool` or
  `function`, as it drops allow-listed spans, before overlap resolution and name linking.
  Simulated: 68 rejections become the same 6, and the text is forwarded as written. A value
  whose whole key is `user` is never a name; longer spans (`current user`, `User Group Ltd`)
  are kept, and dictionary and rule spans are not affected. The same words in
  `detection.allow_list` work today (simulated: the same 6 left), but the allow list applies to
  every detector, and an operator's own list replaces a default one as a whole.
- **C. B, plus the strings each request forwards unchanged.** The dialect would list the
  model, tool names, property names and `required` entries, and the engine would drop
  contextual spans and name forms whose key equals one of them. That clears the remaining 6 and
  the 22 parameter-name rejections, but turns a rejection into a leak: a name that only the ML
  layer finds and that is also a property name or a tool name (the shape of slice 21's
  property `Juan Pérez` listed in `required`) would be forwarded in the text as well.
  Rejected: it does not fail closed.

**Recommendation.** B: a fixed list in a core module, applied to contextual detectors only,
with the audit and the ML threshold unchanged, and usage §6.2 corrected to say that a false
positive equal to a field forwarded unchanged rejects the request, with the allow list as the
workaround. Still open after B, each a rejection and never a leak: the model's name in the text
(`You are Claude` with a `claude-*` model; dropping `Claude` is unsafe because it is also a
person's name), tool names mentioned in the text, and English parameter names that the model or
name linking takes for entities. For parameter names, measure adding role nouns (`customer`,
`owner`, `manager`, `contact`, `recipient`, `client`, `vendor`, `team`, `project`) to the common
and generic word lists of name linking before considering renaming property names, which
slice 21 left out of scope.

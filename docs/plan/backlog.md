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

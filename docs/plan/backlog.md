# Backlog (after v0.1.0)

Not yet cut into slices. Each item needs its own slice file, written to the format in
[README.md](README.md), before anyone implements it. Ordered by expected value.

## 1. Entity linking (aliases)

**Problem.** "Juan Pérez", "Mr. Pérez" and "Juan" get three placeholders, so the model treats
them as three people. Dictionary aliases ("ACME" for "ACME Corporation") have the same problem.

**Direction.** Let a span carry an explicit entity key so several surfaces share one entity, and
make the placeholder encode the variant so restoration stays lossless, for example `[PERSON_1]`
for the full name and `[PERSON_1_B]` for the second surface. Start with explicit dictionary
aliases (deterministic), then token-subset linking for `PERSON` (a single token that equals the
first or last token of exactly one known person). Needs an evaluation set for wrong merges,
which are worse than missed merges.

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

Known gaps in the rules of slice 02, each a missed detection:

- IBANs written in lower case (`es91 2100 …`): the candidate pattern is upper case only, although
  `iban_valid` ignores case. The checksum would still filter false positives.
- Email addresses with a non-ASCII local part (`josé@example.com`).
- A private key pasted without its `-----END … PRIVATE KEY-----` line: the PEM rule needs both
  lines, so the body is forwarded.
- With `URL` enabled, an email inside a URL wins the overlap (score 1.0 against 0.9) and the
  rest of the URL is not pseudonymised.

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

# State of the art and feasibility

Research snapshot: **November 2026**. This document explains what already exists, what the
evidence says about each design choice, and why aliasgate is built the way it is. Benchmarks
quoted here come from the linked sources and were not reproduced by us; slice 11 builds our own
evaluation harness precisely because published numbers disagree with each other.

## 1. The problem

A company forbids sending customer data to an external LLM. The users still want to use the LLM
on documents and conversations that are full of customer data.

The requested solution is a gateway between the user and the LLM that:

1. replaces sensitive values (people, companies, identifiers, contact details, secrets) with
   pseudonyms **before** the request leaves the company network;
2. does so **consistently**, so the model can still reason about who is who;
3. **reverses** the replacement in the model's answer, so the user reads real values.

## 2. Verdict

**Feasible, with two limits that no implementation can remove.** Both must be stated to anyone
who deploys this.

| Limit | Why it cannot be removed | How aliasgate reduces it |
|---|---|---|
| Detection recall is never 100% | Names, companies and addresses are open-ended. Every published detector misses some. | Layered detection (rules, company dictionary, ML model, propagation), fail-closed policy, leak audit, measured recall per entity type. |
| Context can still identify someone | "The CEO of the largest bank in Cantabria" contains no name but identifies one person. | Documented residual risk. Company dictionary for known sensitive terms. This is a data-minimisation control, not anonymisation. |

What *is* fully deterministic and testable:

- the mapping (the same value always gets the same pseudonym inside one request);
- rule-based and dictionary-based detection (regex, checksums, exact terms);
- the restoration of the response, including streamed responses.

What is *not* deterministic in the strict sense: ML-based detection of free-form entities. It is
repeatable (same model, same input, same output) but its recall is a measured statistic, not a
guarantee. The architecture therefore treats the ML layer as one layer among four.

## 3. Existing tools

| Tool | Kind | Reversible | Streaming restore | Tool calls | Notes |
|---|---|---|---|---|---|
| [Microsoft Presidio](https://microsoft.github.io/presidio/) | Detection and anonymisation library | Encrypt/decrypt operators; orchestration is left to the application | No | No | The reference detection framework. Default spaCy NER is weak on non-English names. Has a built-in [GLiNER recognizer](https://microsoft.github.io/presidio/samples/python/gliner). |
| [LiteLLM + Presidio guardrail](https://docs.litellm.ai/docs/proxy/guardrails/pii_masking_v2) | LLM gateway plugin | `output_parse_pii` option | Partial | Partial | The restore path is [reported missing on the native Anthropic route](https://github.com/BerriAI/litellm/issues/22821). |
| LLM Guard (Anonymize + Vault scanners) | Library | Yes, in-memory vault | No | No | Library, not a gateway. Not covered by the search results gathered for this document; listed from prior knowledge. |
| [lexvault](https://github.com/nitishagar/lexvault) | LiteLLM plugin | Yes | Yes (per its README) | Yes (per its README) | Targets proprietary terms (codenames, schema), not general PII. |
| [veil-pii](https://pypi.org/project/veil-pii/) | Python library | Yes | Yes, chunk-split tested | Not confirmed | Uses `⟨EMAIL_1⟩` placeholders, optional encrypted vault. |
| [PrivAiTe](https://pypi.org/project/privaite/0.3.4/) | Self-hosted proxy | Yes | Yes | Yes | Closest in scope to aliasgate. |
| NoPII, Grepture | Hosted services | Yes | Yes | Varies | Hosted: the raw data leaves the company network to reach them, which defeats the purpose for our use case. |

**Reading of the landscape.** The idea is not new and the space is active. What is missing is a
small, self-hosted gateway that (a) keeps no reversible mapping at rest, (b) has correctness
proven by property tests rather than claimed, (c) treats Spanish and other European languages and
identifiers as first-class, (d) handles both major API dialects including streaming and tool
calls, and (e) ships its own evaluation harness. That is the scope of this project.

An alternative considered and rejected for v1: building only a LiteLLM plugin. It would inherit
LiteLLM's per-route gaps and tie correctness to another project's internals. The core of
aliasgate is a plain library with no web dependency, so a LiteLLM adapter remains possible
(see the backlog).

## 4. Detection: what the evidence says

### 4.1 Rules and checksums

Emails, IBANs, card numbers, national identifiers, IP addresses and API keys have a fixed format
and often a checksum. Regex plus checksum validation gives near-perfect precision and recall on
these, costs microseconds, and is fully deterministic. Every serious tool uses this layer. It is
our first layer.

### 4.2 Company dictionary

The requirement includes "everything that can affect a company": customer names, project
codenames, internal product names. No public model knows these. A term list matched exactly is
the only deterministic way to catch them, and it is the layer a compliance team can audit line
by line. lexvault is built entirely on this idea. It is our second layer.

### 4.3 ML models for free-form entities

Names of people, organisations and addresses need a model. Candidates as of November 2026:

| Model | Size | Licence | Languages | Evidence |
|---|---|---|---|---|
| spaCy NER (Presidio default) | small | MIT | per-language models | Weakest on multilingual PII benchmarks: 0.195 partial-match F1 on the 25-language [REDACT leaderboard](https://benchmarklist.com/benchmarks/redact/) (self-reported numbers). |
| GLiNER PII variants (`urchade/gliner_multi_pii-v1`, `knowledgator/gliner-pii-*`) | ~0.2–0.5B | Apache-2.0 (verify per model) | multilingual | Zero-shot labels: new entity types need no retraining. 0.32 on REDACT. |
| [GLiNER2-PII](https://arxiv.org/abs/2605.09973) (`fastino/gliner2-privacy-filter-PII-multi`) | ~0.2–0.3B | **verify on the model card** | en, fr, es, de, it, pt, nl | 42 PII types. Best span F1 on the SPY benchmark among five systems, but low precision (0.35–0.37): it over-predicts. |
| [OpenAI Privacy Filter](https://openai.com/index/introducing-openai-privacy-filter/) (`openai/privacy-filter`) | 1.5B total, ~50M active | Apache-2.0 | cross-lingual | 8 categories. 96% F1 on PII-Masking-300k. Leads most PII datasets in a [32-benchmark evaluation](https://arxiv.org/abs/2608.02616), weaker on general NER. |
| A general LLM as detector | large | n/a | all | Best on REDACT (0.636), but it would mean sending the raw data to an LLM, which is the thing we are preventing. Only an option with a local model. |

Conclusions:

- Published results **conflict** (one leaderboard ranks Presidio above GLiNER, others the
  reverse). The scores depend on the dataset, the span-matching rule and the backend. We must
  measure on our own corpus (slice 11) instead of trusting a leaderboard.
- For this use case **recall matters more than precision**. A false positive costs a little
  utility (a harmless word becomes a placeholder and is restored afterwards). A false negative
  is a leak. A model that over-predicts is acceptable.
- The detector must be **pluggable**. The best model changed twice in 2026 alone.
- Default choice: a multilingual GLiNER-family model, because zero-shot labels let users add
  entity types by configuration and it runs on CPU. OpenAI Privacy Filter is the first
  alternative backend to add.

### 4.4 Propagation

A detector may find "Juan Pérez" in one message and miss it in another. Once a value is known,
finding its other occurrences is a plain string search. This fourth layer is deterministic and
makes the gateway's consistency independent of the model's consistency.

## 5. Replacement: placeholders versus realistic surrogates

Two strategies exist:

- **Typed placeholders**: `Juan Pérez` → `[PERSON_1]`.
- **Realistic surrogates**: `Juan Pérez` → `Carlos Ruiz`, with format-preserving fake emails,
  phones and identifiers.

Evidence:

- Using one generic tag for all names destroys coreference: in a small test on GPT-4o-mini,
  accuracy fell from 90.9% to 27.3% ([source](https://dev.to/privalyse/llm-context-awareness-without-sending-raw-pii-f5a)).
  **Numbered** placeholders avoid this failure.
- [SurrogateShield](https://arxiv.org/abs/2606.29567) (preprint, June 2026) reports that
  surrogates beat placeholders by 13 points of BERTScore on semantic utility. A
  [2025 agent-based approach](https://arxiv.org/abs/2510.27016) reports a five-fold reduction in
  utility errors with semantically consistent pseudonyms.
- Pseudonymisation can degrade some tasks sharply: one study measured a retrieval score drop
  from 0.80 to 0.32 ([source](https://ppc.land/anonymizing-prompts-cuts-openais-gpt-4o-mini-retrieval-score-by-60/)).
  Tasks that depend on the real value (web search for the person, arithmetic on a masked number)
  cannot work by construction.

Decision for v1: **numbered typed placeholders**. Reasons:

1. **Safe reversal.** A placeholder cannot occur in natural text, so restoration never rewrites
   something the model wrote on its own. With surrogates, if the model writes "Mr. Ruiz" or
   declines the name in another language, the surrogate is not restored and the user receives
   fake data that looks real. That is a silent integrity failure.
2. **Auditability.** An unrestored placeholder is visible and countable. A missed surrogate is
   not.
3. **Honesty towards the model.** The model knows the value is masked and does not invent facts
   about a fake person.

Surrogates remain valuable where format matters (a fake but valid-looking email for a model that
must produce a mail-merge template). They are in the backlog as an opt-in per entity type.

## 6. Reversal and streaming

Responses are usually streamed in small chunks. A placeholder can be split across two chunks
(`[PERS` then `ON_1]`). Restoring chunk by chunk without care leaks broken placeholders to the
user. The known correct technique is a **hold-back buffer**: text that could be the beginning of
a placeholder is retained until it is completed or ruled out
([Arcjet](https://arcjet.com/learn/redact-before-openai-anthropic), veil-pii, Laranon all
describe it). The required test is a property test: any way of splitting a response into chunks
must produce the same output as restoring it in one piece.

Tool calls need the same treatment in both directions. The model emits tool arguments as JSON,
so restored values must be JSON-escaped, and streamed argument fragments need their own buffer.

## 7. Where to keep the mapping

Most tools keep a "vault": a store of pseudonym → original value, sometimes encrypted. The vault
is the most sensitive asset in the system, because whoever reads it can reverse everything.

Chat APIs are stateless: the client resends the whole conversation on every request. So the
gateway can rebuild the mapping from the request itself, use it to restore the response, and
discard it. If the placeholder assignment is a deterministic function of the request content,
the earlier turns get the same placeholders every time, which also keeps the provider's prompt
cache valid.

Decision: **no vault in v1. The mapping lives in memory for the duration of one request.**
Nothing reversible is ever written to disk. A persistent encrypted vault is only needed for
stateful APIs (for example OpenAI Responses with `previous_response_id`) and is in the backlog.

## 8. Legal context (EU)

This section is orientation, not legal advice.

- Under the GDPR, pseudonymised data remains personal data for whoever can reverse it. The
  company running the gateway holds the mapping (even if only in memory), so for the company
  nothing changes: this is a security and minimisation measure (Art. 25 and 32), not an
  exemption.
- The [EDPB Guidelines 01/2025 on Pseudonymisation](https://www.edpb.europa.eu/our-work-tools/documents/public-consultations/2025/guidelines-012025-pseudonymisation_es)
  were adopted for consultation in January 2025. They describe the "pseudonymisation domain"
  (who must be unable to attribute the data) and state that unauthorised reversal is a personal
  data breach. Check the EDPB site for the final version.
- In **EDPS v SRB** (CJEU, C-413/23 P, 4 September 2025) the Court confirmed a *relative*
  approach: pseudonymised data is not automatically personal data for a recipient who has no
  reasonable means to re-identify the individuals
  ([Jones Day summary](https://www.jonesday.com/es/insights/2025/09/cjeu-clarifies-scope-of-personal-data-in-edps-v-srb-decision),
  [FPF analysis](https://fpf.org/?p=247273)). This supports the architecture: the LLM provider
  receives data without the key. Commentators note the judgment leaves open how this applies to
  controller-to-processor transfers, and it only helps if the remaining context does not allow
  re-identification, which is the second limit in section 2.

Practical consequence: a company should present this gateway to its DPO as a strong technical
measure that reduces risk, with measured detection rates, not as proof that "no personal data is
sent".

## 9. Design decisions derived from this research

| # | Decision | Section |
|---|---|---|
| D1 | Four detection layers: rules with checksums, company dictionary, pluggable ML model, propagation | 4 |
| D2 | Recall over precision; fail closed on any detection error | 4.3 |
| D3 | Numbered typed placeholders `[TYPE_N]`; surrogates opt-in later | 5 |
| D4 | Tolerant restoration and hold-back buffer for streams, verified by property tests | 6 |
| D5 | Stateless: per-request in-memory mapping, no vault | 7 |
| D6 | Self-hosted, transparent proxy for the OpenAI Chat Completions and Anthropic Messages dialects | 3 |
| D7 | Own evaluation harness with recall per entity type, in English and Spanish first | 4.3 |
| D8 | Honest documentation of residual risk | 2, 8 |

The architecture that implements these decisions is in [architecture.md](architecture.md).

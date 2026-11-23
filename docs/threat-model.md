# Threat model

## What is protected

Personal data of customers and end users, and confidential business identifiers (customer names,
project codenames, account numbers, credentials) contained in prompts, conversation history and
tool results.

## From whom

| Actor | In scope | Protection |
|---|---|---|
| The LLM provider (storage, logs, staff, training, breach on their side) | yes | It receives placeholders instead of values. |
| Network observers between gateway and provider | yes | TLS, plus the above. |
| Readers of gateway logs and metrics | yes | Logs never contain text, values or mappings (architecture §7.1). |
| Someone who steals the gateway's disk | yes | Nothing reversible is stored (architecture §4, decision D5). |
| The user of the gateway | no | The user legitimately sees the real data. |
| An attacker with code execution on the gateway host while it is serving | no | They can read process memory. Protect the host like any system that handles the raw data. |

## Residual risks

These are known and not fully solved. A deployment must accept or mitigate each one.

1. **Missed detections.** A name, company or address that no layer recognises is sent as is.
   The rules recognise only the formats listed in [usage §6.1](usage.md#61-entity-types): an
   identifier written another way, or of another country, is a missed detection too.
   Mitigations: the company dictionary for known terms, a recall-oriented ML threshold,
   propagation, person and organisation name linking, and measuring recall on your own data with the
   [evaluation harness](evaluation.md). Person name linking replaces the shorter forms of a
   detected name, in lower case too unless the word is a common word, so these are still sent
   as written: a common word that is also a name, written in lower case (`gracias rubio`);
   words shorter than `min_propagation_length`; nicknames and diminutives (`Bob` for
   `Robert`); decomposed or full-width forms of a common word, which the leak audit does not
   catch either; and the words of a detected name that is written in lower case. Organisation
   name linking replaces the distinctive words of a detected organisation and its name without
   the legal form, so these are still sent as written: a distinctive word that is a common word
   (`Stone` for `Stone Logistics Ltd`); acronyms (`GHS` for `Global Harbour Shipping`); the
   parts of a hyphenated organisation name (`Yang` of `Yang-Clark Ltd`); words shorter than
   `min_propagation_length`; and, in lower case, the words of an organisation that a detector
   returns in lower case.
2. **Identification by context.** Job titles, rare events, small-town references or combinations
   of facts can identify a person without any detectable entity. No mitigation in the gateway.
3. **Content the gateway cannot read.** Images, audio and file attachments are rejected by
   default. Setting `unsupported_content: pass` forwards them unprotected.
4. **Restoration into tool calls.** When the model calls a tool, the gateway restores real
   values into the arguments, because the client-side tool needs them. A malicious document in
   the prompt (prompt injection) could make the model call a tool that sends data elsewhere, for
   example a URL fetcher. The gateway does not create this risk, but restoring makes the real
   values available to it. Mitigation: restrict the tools the application exposes; per-tool
   restore policies are in the backlog.
5. **Provider-side tools.** Tools executed by the provider (hosted web search, code execution)
   only ever see placeholders, so they cannot use the real values. This is safe but reduces
   usefulness.
6. **Utility loss.** Tasks that depend on the real value (guessing gender from a name,
   arithmetic on a masked number, facts about a real company) degrade or fail. Amounts and dates
   are not pseudonymised by default for this reason. A wrong link misleads the model about who
   did what: a second person named by a word of an earlier one's name (another `Alice`) gets the
   earlier person's variant. One-word names, name parts and `Don` add such links: `Mr. Smith`
   after `Dr. Smith`, the `Marie` of another person after `Anne-Marie`, and `Don` before
   another name after the Spanish `Don Juan`, which reads as a first name and a surname.
   Organisation names add two more: in a later message, another organisation that shares a
   distinctive word with an earlier one gets the earlier one's variant, and a word shared by a
   person and an organisation goes to the person. That is a utility loss, not a leak.
7. **Linkability within a request.** The provider can see that `[PERSON_1]` is the same person
   throughout a conversation, and that the placeholders of a dictionary term and its aliases
   are names of one entity (`[ORG_1_B]` is another name of `[ORG_1]`). It also sees which
   placeholders are shorter forms of one person's or organisation's name (`[PERSON_1_B]` for
   `Johnson` next to `[PERSON_1]` for `Alice Johnson`, `[ORG_1_B]` for `Brightwater` next to
   `[ORG_1]` for `Brightwater Logistics Ltd`). That is intended, since the model needs it.
   Which aliases a dictionary holds is not revealed: letters follow the order of appearance in
   the request.
8. **Unresolved or altered placeholders.** A model may invent `[PERSON_9]` or mangle a
   placeholder beyond the tolerant pattern. The user then sees a placeholder. This is a visible
   failure, not a leak; it is counted in metrics.

## Security invariants

Every change must preserve these. Each has at least one automated test once its slice is done.

1. No request is forwarded unless every slot went through the engine without error.
2. After pseudonymisation, no detected surface is present in any processed slot (leak audit).
3. The mapping is never written to disk, logged, or sent upstream.
4. Logs and metrics never contain request text, response text or surfaces.
5. Unknown endpoints and uninspectable content are rejected by default.
6. Restoration only replaces placeholders that exist in the mapping of the same request.

## Reporting

See [SECURITY.md](../SECURITY.md).

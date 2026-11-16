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
   Mitigations: the company dictionary for known terms, a recall-oriented ML threshold,
   propagation, and measuring recall on your own data with the evaluation harness.
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
   are not pseudonymised by default for this reason.
7. **Linkability within a request.** The provider can see that `[PERSON_1]` is the same person
   throughout a conversation. That is intended, since the model needs it.
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

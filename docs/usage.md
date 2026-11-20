# Using aliasgate

This guide takes you from nothing to a restored answer, then explains how to tune detection,
what changes for your users, and every error the gateway can return. Read the
[threat model](threat-model.md) before you rely on the gateway, and
[deployment.md](deployment.md) before you run it for other people.

## 1. Install

The gateway needs Python 3.12 or later, or Docker.

```bash
pip install aliasgate
```

With [uv](https://docs.astral.sh/uv/), as a command on your `PATH`:

```bash
uv tool install aliasgate
```

The ML detector (a local GLiNER model for names, organisations and addresses) is an optional
extra. It installs PyTorch and needs about 1.5 GB of memory per process (section 6.2):

```bash
pip install 'aliasgate[ml]'
```

Or run the image. `:0.1.0` has rules and dictionaries; `:0.1.0-ml` also has the ML model, baked
into the image so that the container needs no access to Hugging Face:

```bash
docker pull ghcr.io/jcjmenez/aliasgate:0.1.0
```

Check the installation:

```bash
aliasgate version
```

## 2. Configure

Start from [examples/aliasgate.example.yaml](../examples/aliasgate.example.yaml): it lists every
key with its default value and a comment. Every key is optional. An empty file is a valid
configuration (rules only, no dictionary, no ML, OpenAI and Anthropic upstreams, port 8080 on
loopback). Unknown keys are errors, so a typo stops the gateway instead of being ignored.

A small configuration for a company that wants its customers' names protected:

```yaml
detection:
  dictionary:
    files: [customers.yaml]     # relative to this file
  allow_list: ["Example Bank"]  # your own company name: never replaced
```

Any value can be overridden with an environment variable: the prefix `ALIASGATE_`, then the
keys joined by `__`, lists and mappings written as JSON. The environment wins over the file.
Sections merge key by key; any other value is replaced as a whole.

```bash
ALIASGATE_LISTEN__PORT=9000
ALIASGATE_DETECTION__ALLOW_LIST='["Example Bank"]'
```

Do not commit a real configuration or dictionary: they name real customers. The repository's
`.gitignore` already ignores `aliasgate.yaml` and `dictionary*.yaml`.

## 3. Check and serve

`aliasgate check` validates the configuration and the dictionaries, builds the detectors
(loading the model when ML is enabled) and prints a summary:

```console
$ aliasgate check --config examples/aliasgate.example.yaml
configuration: examples/aliasgate.example.yaml is valid
detectors: literal, dictionary, rules
dictionary: 3 terms in 1 file
entity types: CREDIT_CARD, EMAIL, IBAN, IP_ADDRESS, NATIONAL_ID, ORG, PHONE, PROJECT, SECRET, TAX_ID
ml: off
upstreams:
  openai: https://api.openai.com
  anthropic: https://api.anthropic.com
passthrough: none
listen: 127.0.0.1 port 8080
policy: unsupported_content=block, strip_user_identifiers=on, tool_definitions=on, leak_audit=on
propagation: on
metrics: on
```

On a problem it prints one line per problem and exits with code 2. Lines name the field, the
file and the entry, never the value, so they are safe to paste into a ticket:

```console
$ aliasgate check --config broken.yaml
error: listen.port: Input should be less than or equal to 65535
error: dictionary file /etc/aliasgate/customers.yaml, entry 7: missing 'type'
```

`aliasgate serve` runs the gateway until you stop it:

```bash
aliasgate serve --config examples/aliasgate.example.yaml
```

`--host` and `--port` override `listen.host` and `listen.port`. On any address that is not a
loopback address, the command prints a warning: the gateway does not authenticate its clients,
so it must sit behind a trusted network boundary ([deployment.md](deployment.md)). The
`aliasgate` command is the same as `python -m aliasgate`.

With Docker, mount a directory holding `aliasgate.yaml` (and the dictionaries it names) at
`/etc/aliasgate`; nothing is baked into the image. The container listens on `0.0.0.0`, which
prints the warning above; publish the port on loopback unless the network in front of it is
trusted:

```bash
docker run --rm -p 127.0.0.1:8080:8080 -v "$PWD/aliasgate:/etc/aliasgate:ro" ghcr.io/jcjmenez/aliasgate:0.1.0
```

Arguments after the image name replace the default command, for example
`check --config /etc/aliasgate/aliasgate.yaml`. The container runs as an unprivileged user and
works with a read-only file system; [examples/docker-compose.yaml](../examples/docker-compose.yaml)
runs the example configuration that way.

| Command | Does |
|---|---|
| `aliasgate serve --config PATH [--host HOST] [--port PORT]` | Serves the gateway. |
| `aliasgate check --config PATH` | Validates the configuration and dictionaries and prints a summary. |
| `aliasgate demo --config PATH [--show-mapping]` | Pseudonymises standard input (section 5). |
| `aliasgate version` | Prints the version. |

Exit codes: 0 on success, 2 for invalid arguments or an invalid configuration, dictionary or
ML setup, 1 for anything else. An unexpected failure is reported by its exception class only,
because its message could hold text.

## 4. Point your clients at the gateway

The gateway speaks the providers' own APIs, so clients keep working unchanged apart from their
base URL. It holds no provider credentials: the client sends its usual key, and the gateway
forwards the `Authorization`, `x-api-key` and provider headers as it received them.

| Route | Forwarded to |
|---|---|
| `POST /v1/chat/completions` | `upstreams.openai`, OpenAI Chat Completions, streaming included |
| `POST /v1/messages` | `upstreams.anthropic`, Anthropic Messages, streaming included |
| `POST /v1/messages/count_tokens` | `upstreams.anthropic`; the request is pseudonymised, the token count returned as it is |
| `GET /healthz` | answered by the gateway: `{"status": "ok"}` |
| `GET /metrics` | answered by the gateway, when `metrics.enabled` (section 10) |

Every other path is rejected with `404 unknown_endpoint`, unless it is a passthrough entry
(section 4.3). An endpoint the gateway does not understand cannot be pseudonymised, so it is
never forwarded by default.

OpenAI SDK (Python):

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8080/v1")  # the key still comes from OPENAI_API_KEY
```

Anthropic SDK (Python):

```python
import anthropic

client = anthropic.Anthropic(base_url="http://localhost:8080")  # key from ANTHROPIC_API_KEY
```

Both SDKs, and many tools built on them, also read the base URL from the environment:

```bash
export OPENAI_BASE_URL=http://localhost:8080/v1
export ANTHROPIC_BASE_URL=http://localhost:8080
```

Two runnable examples send a synthetic prompt, whole and then streamed, and print the restored
answers and the number of entities the gateway replaced (the `x-aliasgate-entities` header):

```bash
OPENAI_MODEL=gpt-4.1 uv run --with openai python examples/openai_client.py
```

```bash
uv run --with anthropic python examples/anthropic_client.py
```

With the example configuration, the provider receives `[EMAIL_1]`, `[ORG_1]` and `[IBAN_1]`
instead of the email, the company from the example dictionary and the IBAN, and the scripts
print the real values.

### 4.1 Azure OpenAI

Azure OpenAI serves the OpenAI-compatible API under `/openai/v1/`. `upstreams.*.base_url` may
end in a base path, and the request path is appended to it unchanged, so the base path is
`/openai`:

```yaml
upstreams:
  openai:
    base_url: https://YOUR-RESOURCE.openai.azure.com/openai
```

Clients still point at the gateway as for OpenAI (`base_url="http://localhost:8080/v1"`), send
the resource key, which the gateway forwards unchanged, and use the deployment name as `model`.
Both ways of sending the key work: the `api-key` header, and `Authorization: Bearer <key>`,
which is what the plain OpenAI SDK sends (checked on 2026-11-20).

What does not work:

- A `base_url` ending in `/v1`: requests would go to `/v1/v1/...`.
- A query string in `base_url`: the configuration rejects it. The gateway forwards the client's
  own query string.
- Azure's older paths, `/openai/deployments/{name}/chat/completions?api-version=...`, which the
  `AzureOpenAI` client class uses: they are not gateway routes. Use the plain `OpenAI` client.

### 4.2 Claude in Microsoft Foundry

Foundry serves the Messages API under `/anthropic/v1/`:

```yaml
upstreams:
  anthropic:
    base_url: https://YOUR-RESOURCE.services.ai.azure.com/anthropic
```

Clients send the Foundry key in `x-api-key` (what the Anthropic SDK does with `api_key`) and the
deployment name as `model`. `count_tokens` works on Foundry; the Models API does not exist there
(checked on 2026-11-18).

### 4.3 Passthrough entries

An endpoint that carries no customer data, such as a model list, can be forwarded unchanged:

```yaml
upstreams:
  passthrough_paths: ["anthropic GET /v1/models"]
```

An entry is `"<upstream> <METHOD> <path>"`: upstream `openai` or `anthropic`, method `GET`,
`POST` or `DELETE`, and a path of non-empty segments of letters, digits and
`-._~!$&'()*+,;=:@`, with no `%` escapes, query, trailing slash, or `.` and `..` segments. A
method and path may appear in only one entry, so that the path names exactly one upstream; both
providers serve `GET /v1/models`, so only one of them can be passed through. A passthrough
request and its answer are **not pseudonymised**: never list an endpoint that carries
customer data. The gateway answers errors on a passthrough route in its upstream's error shape.

## 5. Dictionaries and the allow list

Rules find values with a fixed shape. Names of customers, projects and codenames need a
dictionary:

```yaml
# customers.yaml
terms:
  - term: Globex S.L.
    type: ORG
  - term: Project Falcon
    type: PROJECT      # your own entity types are allowed: upper case, [A-Z_], 40 characters
  - term: "Yes Bank"   # quote terms YAML would read as something else (yes, no, numbers)
    type: ORG
```

A term matches as a whole term, ignoring case, with any whitespace between its words. Where
several terms match at one position, the longest wins; a dictionary match also beats a rule or
the ML model on the same text. The same term with two types, in one file or across files, is an
error. List several files under `detection.dictionary.files`; relative paths are resolved
against the configuration file's directory.

The **allow list** holds terms that are never replaced, typically your own company's and
products' names, which the ML model or a dictionary would otherwise replace. A detected value is
dropped when it is the same as an allow-list entry after normalisation (Unicode NFKC, case
folding, whitespace collapsed). It must be the whole value: allowing `ACME` does not keep
`ACME Corporation`.

Try both on sample text with `aliasgate demo`, which reads standard input and prints the
pseudonymised text, with counts by entity type on standard error:

```console
$ echo "Ask Globex S.L. about Project Falcon, mail juan.perez@example.com" | aliasgate demo --config aliasgate.yaml
Ask [ORG_1] about [PROJECT_1], mail [EMAIL_1]
entity type  entities  occurrences
EMAIL               1            1
ORG                 1            1
PROJECT             1            1
```

`--show-mapping` also prints each placeholder and the value it replaced. That is the only place
where aliasgate prints detected values: use it on your own terminal, never in CI logs or shared
sessions. `demo` never runs inside `serve`.

## 6. Tuning detection

Detection runs in layers, and where two results overlap, the higher layer wins: placeholder
literals already in the text, then dictionaries, then rules, then ML, then propagation. Measure
the effect of a change with the [evaluation harness](evaluation.md) on samples of your own data.

### 6.1 Entity types

`detection.rules.enabled_types` selects the rules. The defaults are `EMAIL`, `PHONE`, `IBAN`,
`CREDIT_CARD`, `NATIONAL_ID` (Spanish DNI and NIE, US SSN), `TAX_ID` (Spanish CIF),
`IP_ADDRESS` and `SECRET` (keys and tokens with a known prefix, such as `sk-`, GitHub, AWS and
Slack tokens, and PEM private keys). `URL` exists
and is off by default, because URLs are often needed as they are. Card, IBAN and identity
numbers are checked with their checksums, phone numbers with `phonenumbers`; numbers without an
international prefix are read in the regions of `detection.rules.phone_regions` (`ES` and `US`
by default). Amounts and dates are never replaced: the model needs them, and they rarely
identify anyone on their own.

### 6.2 The ML detector

Names, organisations and addresses that no dictionary lists need the ML detector. It needs the
`ml` extra (or the `-ml` image) and `detection.ml.enabled: true`; enabled without the extra, the
gateway refuses to start and names the install command.

- **Model.** `urchade/gliner_multi_pii-v1`, licensed Apache-2.0, trained for English, French,
  German, Spanish, Portuguese and Italian. Its base encoder, `microsoft/mdeberta-v3-base`, is
  MIT-licensed. Other GLiNER models work if they accept the configured labels.
- **Pinning.** `model` and `revision` (a commit of the model repository) are set together; the
  default revision is pinned. Loading the model also fetches the tokenizer of
  `microsoft/mdeberta-v3-base` at its default branch, which the pin does not cover. The `-ml`
  image downloads both at build time and runs with `HF_HUB_OFFLINE=1`, so what it loads cannot
  change after the build. For a model in a local directory, set `model` to the directory and
  `revision: null`.
- **Downloads.** Outside the image, the model (about 1.2 GB) is downloaded from Hugging Face at
  the first start and cached in `~/.cache/huggingface`. Set `HF_HUB_OFFLINE=1` once it is
  cached.
- **`threshold`** (0.4) is the score from which a model result counts. It is low on purpose: a
  missed name is a leak, an extra one only costs utility. The model over-predicts; it may take a
  phrase such as "the meeting with the client" for a person.
- **`labels`** maps model labels to entity types (`person: PERSON`, `organization: ORG`,
  `address: ADDRESS`). `ALIASGATE_DETECTION__ML__LABELS` replaces the file's labels; it does not
  merge with them.
- **`window_words`** (250) and **`overlap_words`** (50). Long texts are cut into overlapping
  windows. Both count words as the model does: a run of letters and digits, or one punctuation
  mark, so a CSV line has many more words than it seems. The model reads at most 384 words per
  window; the gateway refuses a window it would truncate, and runs one full window at start-up,
  so a `window_words` the model cannot take stops the gateway before it serves a request.
- **`cache_entries`** (4096). Conversations resend their history, so results are cached per
  text, in memory only, holding spans and no text. 0 disables the cache.

Measured on an Apple M5 Pro CPU: about 1.5 GB of resident memory per process, about 7 s to
start with the model cached, and about 140 ms for 200 words. One inference runs at a time per
process.

### 6.3 Propagation

With `detection.propagate` (on by default), every value detected once is also replaced
everywhere else in the same request, ignoring case and whitespace, when it is at least
`min_propagation_length` (3) characters long. This catches a name the model found in one message
and missed in the next. Keep it on. If short values cause replacements you do not want, raise
`min_propagation_length` instead.

With propagation off, a value detected in one place and missed in another stays in the outbound
body, and the leak audit (section 9) rejects the request with `leak_audit_failed`.

## 7. What your users will notice

- **Placeholders in reasoning.** Anthropic `thinking` and `redacted_thinking` blocks are signed
  by the provider and passed back unchanged, so the model's reasoning shows `[PERSON_1]`, not
  the name. Changing them would break the signature on the next turn.
- **Unresolved placeholders.** A model may invent `[PERSON_9]` or alter a placeholder beyond
  what restoration tolerates (case, a space for the last underscore, a few spaces inside the
  brackets, Markdown escapes). The user then sees the placeholder. That is a visible failure,
  not a leak, and it is counted (`aliasgate_unresolved_placeholders_total`).
- **Case variants merge.** `ACME` and `Acme` in one request are one entity, restored as the
  form seen first.
- **Tasks that degrade.** Anything that depends on the real value: guessing gender or origin
  from a name, arithmetic on a masked number, facts about a real company, provider-side tools
  such as hosted web search, which only see placeholders. Models sometimes comment on the
  placeholders ("unfilled template placeholders"), and the user then reads that sentence with
  the real values restored into it.
- **Rejected attachments.** Images, audio, documents and files cannot be inspected, so
  `policy.unsupported_content: block` (the default) rejects them with `422 unsupported_content`.
  `pass` forwards them unprotected.
- **Real values in tool calls.** Tool-call arguments are restored, because your tools need the
  real values. A prompt injection could make the model call a tool that sends them elsewhere:
  expose only tools you trust ([threat model](threat-model.md), risk 4).
- **Preserved thinking.** Current Claude models reject a replayed thinking block when anything
  before it changed. Earlier turns are pseudonymised identically on every turn, with three
  exceptions, each of which changes an earlier message on the next turn: propagation, when a
  later turn reveals an entity an earlier text contained undetected; a placeholder variant the
  model wrote (`[person_1]`), which is restored and then sent again as `[PERSON_1]`; and an
  unresolved placeholder in an assistant message (`[PERSON_9]`), which is sent again as
  `[LITERAL_1]`. The provider then rejects the thinking blocks after the change.

### Fields forwarded unchanged

The gateway pseudonymises message text, system prompts, tool results, tool-call arguments and
the `description` values of tool definitions. These fields are forwarded as they are:

- OpenAI `metadata`, and `prompt_cache_key`, which OpenAI recommends in place of `user` and
  which may therefore hold a per-user id;
- `web_search_options.user_location` (city, region, timezone);
- `messages[].name`, tool names, parameter names, `enum` values and the keys of objects inside
  tool-call arguments;
- `response_format` schema descriptions and `stop` sequences;
- under `unsupported_content: pass`, image, audio and file parts.

End-user identifiers (`user`, `safety_identifier`, Anthropic `metadata.user_id`) are removed by
default (`policy.strip_user_identifiers`). A detected value inside any forwarded field makes the
leak audit reject the request.

## 8. Error codes

Errors the gateway answers itself use the error shape of the route's API. OpenAI:
`{"error": {"message": ..., "type": "aliasgate_error", "param": null, "code": "<code>"}}`.
Anthropic: `{"type": "error", "error": {"type": ..., "message": "... (<code>)"}}`. A request to
an unknown path gets the Anthropic shape when it carries an `anthropic-version` or `x-api-key`
header. Errors answered by the provider are passed through, with placeholders restored in their
messages. Every response, errors included, carries `x-aliasgate-request-id`, which the log line
of the request also holds.

| Code | Status | Outcome | Cause | Remedy |
|---|---|---|---|---|
| `invalid_json` | 400 | `rejected` | The body is not a JSON object encoded as UTF-8 (this includes a lone surrogate escape). | Send a JSON object. |
| `malformed_request` | 400 | `rejected` | The body lacks the shape the API requires; the message names the field. | Fix the request; the provider would reject it too. |
| `unknown_endpoint` | 404 | `rejected` | The method and path are neither a route nor a passthrough entry. `GET /metrics` answers this when metrics are off. | Use a supported route (section 4), or add a passthrough entry for an endpoint without customer data. |
| `request_too_large` | 413 | `rejected` | The body is larger than `listen.max_request_bytes` (10 MiB). | Raise the limit, or send less. |
| `unsupported_content` | 422 | `rejected` | Content the gateway cannot inspect (image, audio, document, file, an unknown block type) under `policy.unsupported_content: block`; the message names the kind. | Remove it, or set `pass` knowing it is forwarded unprotected. |
| `leak_audit_failed` | 500 | `rejected` | A detected value was still somewhere in the outbound body. Typical causes: a field forwarded unchanged (OpenAI `metadata`, tool names, `enum` values, `model`); `propagate: false`; a variant with the same key that propagation does not replace (`STRASSE` for `Straße`, decomposed accents, full-width letters). | Move the value out of that field, turn propagation on, or write the value consistently. `policy.leak_audit: false` turns the audit off, which removes the last check before data leaves. |
| `pseudonymization_failed` | 500 | `gateway_error` | A detector raised; the log line names it. | Check the `request failed` log line (`detector`); for the ML detector, its installation and memory. |
| `internal_error` | 500 | `gateway_error` | An unexpected failure in the gateway, the leak audit itself included. Nothing was forwarded. | Look up the log lines by request id, and report the exception class. |
| `restoration_failed` | 502 | `gateway_error` | The provider's answer could not be restored; it is never returned un-restored. | Look up the log lines by request id, and report the exception class. |
| `upstream_unreachable` | 502 | `upstream_error` | The provider could not be reached (DNS, connection, TLS). | Check `upstreams.*.base_url` and the egress path. |
| `upstream_timeout` | 504 | `upstream_error` | The provider did not answer within `upstreams.timeout_seconds` (600). | Raise the timeout, or stream long answers. |

A streamed answer that fails after it started cannot change its status: the stream ends early,
and text the gateway still held back is never sent.

## 9. Safety checks

Before forwarding, the **leak audit** searches every string value of the outbound body, not only
the fields the gateway pseudonymised, for every detected value of at least
`min_propagation_length` characters: as a whole term ignoring case, and after Unicode NFKC
normalisation and case folding. If one is found, the request is rejected with
`leak_audit_failed` and nothing is sent. It is on by default (`policy.leak_audit`).

After restoring, placeholders in the answer that are not in the request's mapping are left as
they are and counted as unresolved.

## 10. Logs, headers and metrics

**Logs.** The gateway writes JSON lines to standard error, one per request when it ends:
`request_id`, `route`, `status`, `duration_ms`, `slots` (strings processed), `entities` (unique
entities by type) and `unresolved_placeholders`. Failures add a `request failed` line with the
`stage` and the exception class, and a leak a `leak audit failed` line with counts and types.
Logs never contain request or response text, detected values or mappings. Uvicorn's access log
is off, and the `httpx` and `httpcore` loggers stay at `WARNING`, because they would log the
forwarded query string and headers: lowering them breaks the logging rule.
`logging.level: DEBUG` adds no text either.

**Headers.** Every response carries `x-aliasgate-request-id` and `x-aliasgate-entities`, the
number of entities in the request's mapping (0 when nothing was replaced).

**Metrics.** Prometheus metrics are served at `GET /metrics` on the gateway's own port when
`metrics.enabled` (the default). Label values are chosen by the gateway (routes such as
`POST /v1/messages`, `unknown` for an unrouted request, status classes, outcomes, error codes,
detector names, entity types from the configuration); none is taken from a request. Do not
expose `/metrics` outside your network.

| Metric | Type | Labels |
|---|---|---|
| `aliasgate_requests_total` | counter | `route`, `status_class`, `outcome` |
| `aliasgate_entities_total` | counter | `entity_type`, `source` (detector name, or `propagation`) |
| `aliasgate_unresolved_placeholders_total` | counter | `route` |
| `aliasgate_leak_audit_failures_total` | counter | `route`, `entity_type` |
| `aliasgate_rejections_total` | counter | `route`, `code` |
| `aliasgate_detection_seconds` | histogram | `detector` |
| `aliasgate_request_overhead_seconds` | histogram | `route`, `phase` (`pseudonymize`, `restore`) |
| `aliasgate_detection_cache_total` | counter | `result` (`hit`, `miss`); only with the ML detector's cache |

`outcome` is one of:

- `ok`;
- `rejected`, the gateway refused the request: `invalid_json`, `malformed_request`,
  `unknown_endpoint`, `request_too_large`, `unsupported_content`, `leak_audit_failed`;
- `upstream_error`, the provider failed: `upstream_unreachable`, `upstream_timeout`, any error
  status the provider answered, and a stream that broke while reading from the provider;
- `gateway_error`, the gateway failed: `pseudonymization_failed`, `internal_error`,
  `restoration_failed`, and a stream that failed while restoring or sending.

A client that goes away during a stream counts as `ok`. `aliasgate_rejections_total` counts
every error response the gateway produced, by its code.

Suggested alerts: any increase of `aliasgate_leak_audit_failures_total`; a rise of
`aliasgate_rejections_total` by `code`; a rising `aliasgate_unresolved_placeholders_total`,
which shows that a model has started altering placeholders.

Do not set `PROMETHEUS_MULTIPROC_DIR` unless metric files on disk are acceptable to you: with it,
the Prometheus client writes metric values (counts, never text) to files in that directory.

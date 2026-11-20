# Slice 12: CLI, Docker image, usage documentation, first release

## Goal

Someone who has never seen the project can install it, configure it, run it, point a client at
it and understand its limits in fifteen minutes. Version 0.1.0 is tagged and published.

## Depends on

Slices 09, 10 and 11.

## Dependencies to add

None. The CLI uses `argparse`.

## Files

| File | Action |
|---|---|
| `src/aliasgate/cli.py` | create |
| `src/aliasgate/__main__.py` | change: delegate to `cli.main` |
| `pyproject.toml` | change: `[project.scripts] aliasgate = "aliasgate.cli:main"`, version `0.1.0` |
| `Dockerfile`, `.dockerignore` | create |
| `.github/workflows/release.yml` | create |
| `docs/usage.md`, `docs/deployment.md`, `CHANGELOG.md` | create |
| `examples/openai_client.py`, `examples/anthropic_client.py`, `examples/docker-compose.yaml` | create |
| `README.md` | change: quick start, status, remove the pre-alpha notice wording |
| `tests/test_cli.py` | create |

## Specification

### CLI

```text
aliasgate serve  --config PATH [--host HOST] [--port PORT]
aliasgate check  --config PATH
aliasgate demo   --config PATH [--show-mapping]     # reads text from stdin
aliasgate version
```

- `serve`: load settings, apply overrides, run uvicorn with `log_config=None` and the project's
  JSON logging. If the host is not a loopback address, print one warning line to stderr: the
  gateway has no client authentication and must sit behind a trusted network boundary.
- `check`: load and validate the configuration and dictionaries, build the pseudonymizer
  (loading the model if enabled), print a summary (enabled detectors, number of dictionary
  terms, entity types, upstreams) and exit 0. On a validation error print a readable list of
  problems (field path and reason, never dictionary content) and exit 2.
- `demo`: pseudonymise stdin and print the result to stdout, and a table of entity types and
  counts to stderr. With `--show-mapping`, also print placeholder → value pairs to stderr. This
  is a local debugging aid for configuring dictionaries; it is the only code path that prints
  surfaces, it only runs on explicit request, and it never runs inside `serve`.
- Exit codes: 0 success, 2 configuration error, 1 anything else.
- `main(argv: Sequence[str] | None = None) -> int` for testability.

### Dockerfile

- Multi-stage. Build stage uses the official uv image to create a virtual environment from the
  lock file (`uv sync --locked --no-dev`). Final stage is `python:3.13-slim`, copies the
  environment, runs as a non-root user, `EXPOSE 8080`, `HEALTHCHECK` on `/healthz`,
  `ENTRYPOINT ["aliasgate"]`, `CMD ["serve", "--config", "/etc/aliasgate/aliasgate.yaml", "--host", "0.0.0.0"]`.
- Build argument `EXTRAS` (default empty; `ml` adds the ML extra). The ML image pre-downloads
  the pinned model at build time into the image so that the container needs no internet access
  to Hugging Face at run time, and sets `HF_HUB_OFFLINE=1`.
  (Found in slice 09: loading `urchade/gliner_multi_pii-v1` also downloads the config and
  tokenizer of `microsoft/mdeberta-v3-base`, at its default branch, unpinned. Pre-download by
  building the detector once, with `load_gliner` and the configured model and revision, so that
  both land in the cache, and then check that the image starts with `HF_HUB_OFFLINE=1`. The ML
  extra needs `protobuf` for that tokenizer.)
- No configuration or dictionary is baked in. They are mounted.
- Pin base images by digest.

### Release workflow

Triggered by pushing a tag `v*`:

1. run the full CI checks;
2. `uv build`;
3. publish to PyPI with trusted publishing (OIDC, `pypa/gh-action-pypi-publish`), no stored
   token;
4. build and push the two images (`:X.Y.Z` and `:X.Y.Z-ml`) to GitHub Container Registry;
5. create a GitHub release with the changelog section.

The maintainer must configure the PyPI trusted publisher and the `pypi` environment by hand
before the first tag. Write these manual steps in the pull request description. **Do not create
the tag or publish yourself; the maintainer does that.**

### Documentation

`docs/usage.md`
- Install (pip, uv, Docker), minimal configuration, `aliasgate check`, `aliasgate serve`.
- Pointing clients at the gateway: OpenAI SDK (`base_url="http://localhost:8080/v1"`),
  Anthropic SDK (`base_url="http://localhost:8080"`), environment variables
  (`OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL`), with the runnable examples.
- Writing a dictionary and an allow list. Tuning: which entity types to enable, the ML
  threshold, when to turn propagation off. (Found in slice 09: document the ML section,
  installing the `ml` extra, the model licence and its languages, and pinning `revision` with
  `model` (`null` for a local directory). `window_words` counts model words, where a
  punctuation mark is one word, and stays at or below the model's limit of 384. Say that
  `ALIASGATE_DETECTION__ML__LABELS` replaces the file's labels.)
- What the user will notice: placeholders in model reasoning, unresolved placeholders, tasks
  that degrade (list from the threat model), rejected images and files.
- Every error code the gateway can return, with cause and remedy, in one table. (Found in
  slice 10: `leak_audit_failed` (500) means a detected value was still somewhere in the
  outbound body. Typical causes are a field forwarded unchanged (OpenAI `metadata`, tool names,
  `enum` values, `model`), `propagate: false`, or a variant with the same key that propagation
  does not replace (`STRASSE` for `Straße`, decomposed accents, full-width letters).
  `policy.leak_audit: false` turns the audit off; say that this removes the last check before
  data leaves.)
- Metrics (slice 10): list them with their labels, the `outcome` values and which error codes
  fall under each one (slice 10 decisions). Suggest alerts on
  `aliasgate_leak_audit_failures_total`, `rejections_total` by code and rising
  `unresolved_placeholders_total`. Document the `x-aliasgate-entities` response header. Do not
  set `PROMETHEUS_MULTIPROC_DIR` unless metric files on disk are acceptable.
- Notes carried over from earlier slices: `metadata` in OpenAI requests is not stripped; tool
  names and enum values are not pseudonymised. Also forwarded unchanged (slice 05, decided with
  the maintainer): `prompt_cache_key`, which OpenAI recommends in place of `user` and which may
  therefore hold a per-user id; `web_search_options.user_location` (city, region, timezone);
  `messages[].name`; the keys of objects inside tool-call arguments; `response_format` schema
  descriptions; `stop` sequences. Under `unsupported_content: pass`, image, audio and file parts
  are forwarded as they are.
- Upstreams with a base path (added after slice 07): `upstreams.*.base_url` may end in a base
  path, and the request path is appended to it unchanged. Document it with Azure OpenAI, which
  serves the OpenAI-compatible API only under `/openai/v1/`:
  `base_url: https://<resource>.openai.azure.com/openai`. Clients still point at the gateway as
  for OpenAI (`base_url="http://localhost:8080/v1"`), send the resource key, which the gateway
  forwards unchanged (the `api-key` header worked on 2026-11-17), and use the deployment name
  as `model`. Say what does not work: a `base_url` ending in `/v1` (requests go to
  `/v1/v1/...`), a query string in `base_url` (the gateway forwards the client's own query
  string), and Azure's older `/openai/deployments/{name}/...?api-version=...` paths, which are
  not gateway routes. Verify the Azure path and header again when writing the document, and run
  the manual check of this slice against Azure as well, if a resource is available.
- Anthropic clients (found in slice 08): Claude in Microsoft Foundry works with
  `upstreams.anthropic.base_url: https://<resource>.services.ai.azure.com/anthropic`, the key
  in `x-api-key`, and the deployment name as `model` (checked on 2026-11-18). Document the
  passthrough format `"<upstream> <METHOD> <path>"`, the plain-segment path rule, and that a
  method and path can name only one upstream. Document the preserved-thinking caveat in the slice 08 decisions: an earlier
  turn that changes on resend (propagation, a model-written placeholder variant, an unresolved
  placeholder) makes the provider reject the thinking blocks after it.
- Logging (slice 06): one JSON line per request with `request_id`, `route`, `status`,
  `duration_ms`, `slots`, `entities` and `unresolved_placeholders`. Uvicorn's access log is off.
  The `httpx` and `httpcore` loggers stay at WARNING, because they would log the forwarded
  query string and headers. Say that lowering them breaks the logging rule.

`docs/deployment.md`
- Network placement: inside the trust boundary, the only egress path to the provider (block
  direct access to provider domains at the firewall, otherwise the gateway is optional and
  therefore useless).
- TLS and client authentication are delegated to the ingress or service mesh.
- Do not expose `/metrics`. Metrics list and suggested alerts (`leak_audit_failures_total > 0`,
  rising `unresolved_placeholders_total`, `rejections_total` by code).
- Resource sizing with and without ML, from your measurements. (Found in slice 09, on an
  Apple M5 Pro CPU: one loaded model takes about 1.5 GB of resident memory, start-up with the
  model cached takes about 7 s, and 200 words take about 140 ms. Detection runs one inference
  at a time per process.)
- A deployment checklist that ends with: run the evaluation on your own samples and review the
  threat model with your DPO. (Found in slice 11: the command is
  `python -m aliasgate.eval --config <file> <corpus.jsonl>...`, documented in
  `docs/evaluation.md`, including how to handle real samples. `aliasgate.eval` ships in the
  wheel; the synthetic corpora and `tools/generate_corpus.py` do not.)

`CHANGELOG.md` in Keep a Changelog format with the 0.1.0 entry.

### README

Replace the status notice with: alpha, implemented scope, link to usage. Add a five-line quick
start. Keep the "What it does not guarantee" section unchanged.

### Decisions made while implementing (slice 12)

- CLI. `--config` is required by `serve`, `check` and `demo`, as specified; slice 06 had made
  it optional for `python -m aliasgate`, which now needs a command. `tests/proxy/test_main.py`
  was replaced by `tests/test_cli.py`. `--host` and `--port` are validated by `ListenSettings`,
  so an invalid port is reported as `listen.port`. Loopback means `localhost` (any case) or an
  IP loopback address; any other host warns, hostnames and bracketed IPv6 included.
- Exit codes. 2: invalid arguments, invalid YAML (no parser message, which quotes the file),
  validation errors (one line per field, pydantic's reason, never the input), an `ALIASGATE_*`
  variable that does not parse, an unreadable configuration or dictionary, dictionary errors
  (every file is checked and all problems are printed together), and a `ValueError` or
  `RuntimeError` while building the detectors (the same term with two types across files, a
  missing ML extra, a window the model would truncate). 1: a detector failure in `demo` (named
  by detector), and anything else, by exception class only. A failed model download is an
  `OSError` and lands in 1.
- `serve` loads the dictionaries before it builds the detectors, so their problems are listed
  per file, prints the warning before the model loads, and passes the pseudonymizer it built to
  `create_app` (whose docstring now says so).
- `check` prints the detectors, the dictionary term and file counts (never terms), the entity
  types (rules, dictionaries and, with ML, the labels' types), the ML model and revision, the
  upstreams, passthrough entries, listen address, policy, propagation and metrics.
- `demo` treats all of standard input as one text, as one request would. Its table has, per
  type, the entities and the replaced occurrences. `--show-mapping` prints each surface as a
  JSON string, so whitespace inside it stays visible.
- Torch from the CPU index, decided with the maintainer. PyPI's Linux torch wheels pull in
  CUDA 13 (about 2.4 GB of wheels, 6 GB installed). `torch>=2.15.0` is now listed in the `ml`
  extra, and `tool.uv.sources` takes it from `https://download.pytorch.org/whl/cpu` on Linux
  (`explicit = true`, so only torch comes from there). The lock lost the CUDA packages and
  gained `torch 2.15.0+cpu` with its hashes. `pip install 'aliasgate[ml]'` still gets PyPI's
  torch, because uv sources are not part of the wheel's metadata.
- Dockerfile. uv 0.11.26 is copied from its official image into a build stage on the same
  `python:3.13-slim` digest as the final stage, so the virtual environment's interpreter path
  is valid in both. Dependencies are installed from the lock in their own layer, then the
  project with `--no-editable`. The ML image downloads the model with `load_gliner`, then
  proves with `HF_HUB_OFFLINE=1` that `build_pseudonymizer` (warm-up included) works from the
  cache alone. huggingface_hub writes some cache files as 0600, so the cache is made readable to
  all; without that, the non-root user logged a permission error at start-up. Both images set
  `HF_HUB_OFFLINE=1`. The health check uses `http.client`, which ignores proxy variables.
- Release workflow. `ci.yml` gained `workflow_call`, and the release calls it first. The tag
  must equal `v` plus the package version. Order: CI, build, PyPI, images, GitHub release.
  Changed before the first tag: images before PyPI, because a PyPI version can never be
  uploaded again, so a failed image build must happen while the tag can still be moved.
  Third-party actions are pinned by commit. GHCR and `gh release` use the job's own
  `github.token`; nothing is stored. Images are built for `linux/amd64` and `linux/arm64` with
  QEMU, decided with the maintainer. The changelog section stops at the next heading or at the
  link definitions.
- Tests beyond the list: the documented error-code table (code, status, outcome) and both
  metrics tables are compared with the code; the Dockerfile (digests, non-root user, no
  configuration), `.dockerignore`, the release workflow (no secret, OIDC on the PyPI job only,
  pinned actions) and the compose example are checked statically, and the example clients
  compile.
- Example clients print the restored answers, `x-aliasgate-entities` and token usage. The
  OpenAI one needs `OPENAI_MODEL`; the Anthropic one defaults to `claude-opus-5-5` with effort
  `low`. Both cap `max_tokens` at 300.
- Manual checks, 2026-11-20, Apple M5 Pro, Docker 29.8.2 (arm64). Both images build. The core
  image is 214 MB uncompressed (57 MB compressed), the ML image 2.36 GB (1.1 GB of it the
  model). Both start with the configuration mounted read-only, `--read-only`, `--tmpfs /tmp`,
  `--cap-drop ALL` and `no-new-privileges`, answer `/healthz`, report `healthy`, and run as
  uid 10001. The ML image starts with `--network none` in 8 s and uses 1.7 GiB; `demo` inside
  it found a person name. The compose example starts and rejects an image part with 422. The
  wheel, installed with pip in a fresh environment, followed `docs/usage.md` as written.
- Real providers, 2026-11-20, through the wheel's `aliasgate serve`: `examples/openai_client.py`
  (openai 3.57.0) against Azure OpenAI gpt-4.1 with `base_url: https://<resource>.openai.azure.com/openai`,
  the key sent as `Authorization: Bearer` by the SDK, and `examples/anthropic_client.py`
  (anthropic 2.11.0) against Claude Sonnet 5.5 in Microsoft Foundry. Both answered, whole and
  streamed, with the email, company and IBAN restored; the gateway logs held none of them. Four
  billed calls: 102 input and 125 output tokens on gpt-4.1, 152 and 268 on Claude, about
  USD 0.004. Two attempts hit the known intermittent TLS failure to Azure, were answered
  `502 upstream_unreachable` without reaching the provider, and the SDK retried them.
- Found, not fixed: transformers 5.22.2 warns at model load that the mdeberta tokenizer has an
  "incorrect regex pattern" (`fix_mistral_regex`); the slice 09 and 11 measurements already ran
  with it. torch and huggingface_hub print deprecation warnings at start-up. Claude remarked on
  the placeholders in one answer (threat model, risk 6).

## Tests

`test_cli.py`
- `version` prints the package version, exit 0.
- `check` with the example configuration → exit 0 and a summary containing the detector names.
- `check` with an invalid file → exit 2; stderr names the field; stderr does not contain a
  dictionary term from a deliberately broken dictionary fixture.
- `demo` with stdin `"Contact Juan Pérez at juan.perez@example.com"` and a dictionary → stdout
  has placeholders; stderr has counts; no surfaces on stderr without `--show-mapping`; surfaces
  present with it.
- `serve` argument parsing produces the expected host and port (patch `uvicorn.run`); a
  non-loopback host prints the warning.

Manual verification to report in the pull request:
- `docker build` of both variants; the container starts with a mounted config, `/healthz`
  answers, it runs as non-root (`docker exec … id`), and the ML variant starts with networking
  disabled (`--network none`).
- both example client scripts run against the gateway with a real provider key and synthetic
  prompts, streaming and non-streaming.

## Acceptance checklist

- [ ] A new user can follow `docs/usage.md` from zero to a restored answer. Follow it yourself
      on a clean checkout and fix every step that did not work as written.
- [ ] Docker images run as non-root, with no baked-in configuration.
- [ ] The release workflow uses trusted publishing and stores no secret.
- [ ] No tag was pushed and nothing was published by the implementer.
- [ ] Error-code table in the usage document matches the codes in the source (write a test that
      extracts the codes from the document and from `errors.py` and compares the sets).
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Helm chart, Terraform modules.
- A web interface.
- Client authentication inside the gateway.

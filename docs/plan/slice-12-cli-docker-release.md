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
  threshold, when to turn propagation off.
- What the user will notice: placeholders in model reasoning, unresolved placeholders, tasks
  that degrade (list from the threat model), rejected images and files.
- Every error code the gateway can return, with cause and remedy, in one table.
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
  passthrough format `"<upstream> <METHOD> <path>"` and that a method and path can name only
  one upstream. Document the preserved-thinking caveat in the slice 08 decisions: an earlier
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
- Resource sizing with and without ML, from your measurements.
- A deployment checklist that ends with: run the evaluation on your own samples and review the
  threat model with your DPO.

`CHANGELOG.md` in Keep a Changelog format with the 0.1.0 entry.

### README

Replace the status notice with: alpha, implemented scope, link to usage. Add a five-line quick
start. Keep the "What it does not guarantee" section unchanged.

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

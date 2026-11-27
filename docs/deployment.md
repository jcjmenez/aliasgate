# Deploying aliasgate

aliasgate is a technical control: it only protects data that has no other way to reach the
provider. This document says where to put it, what to put in front of it, how big to make it,
and what to check before real data goes through it. [usage.md](usage.md) covers configuration
and clients, and the [threat model](threat-model.md) the risks that remain.

## Network placement

- **Inside your trust boundary.** Run the gateway on hosts you control, in the network zone
  where the raw data already lives. Anyone who can read its process memory can read the
  requests (threat model: an attacker with code execution on the host is out of scope).
- **The only egress path to the provider.** Block direct access to the provider domains at the
  firewall or egress proxy for every client that the gateway serves: `api.openai.com`,
  `api.anthropic.com`, and your Azure endpoints (`*.openai.azure.com`,
  `*.services.ai.azure.com`) or other providers. Allow those domains only from the gateway. If
  clients can still reach the provider directly, the gateway is optional, and an optional
  control protects nothing.
- **No client authentication inside the gateway.** It forwards the provider key each client
  sends and checks nothing else. TLS towards clients and client authentication belong to the
  ingress, reverse proxy or service mesh in front of it (mTLS, an authenticating proxy, network
  policies). `aliasgate serve` warns when it listens on a non-loopback address for this reason.
- **Outbound connections.** The gateway connects to the configured `base_url`s with TLS
  certificate verification, follows no redirects, and honours the standard `HTTPS_PROXY`,
  `NO_PROXY` and `SSL_CERT_FILE` variables for a corporate egress proxy.
- **Do not expose `/metrics`.** Its labels hold no text, but request rates and entity counts
  are information about your traffic. Scrape it from inside the network, or set
  `metrics.enabled: false`.

## The container

The images run as an unprivileged user (uid 10001), contain no configuration and need no
writable file system. Mount the configuration and dictionaries read-only at `/etc/aliasgate`:

```bash
docker run -d --name aliasgate -p 127.0.0.1:8080:8080 \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  -v /srv/aliasgate:/etc/aliasgate:ro ghcr.io/jcjmenez/aliasgate:0.8.0
```

- The image has a `HEALTHCHECK` on `GET /healthz`; use the same path for readiness and liveness
  probes. With the ML model, the gateway answers only after the model is loaded and has run once,
  so allow for the start-up time below.
- The `-ml` image needs no network access except to the provider: the model and its tokenizer
  are in the image and `HF_HUB_OFFLINE=1` is set. It starts with `--network none`.
- Logs are JSON lines on standard error. They hold ids, counts, entity types, durations and
  status codes, never text ([usage.md](usage.md), section 10). Every request writes one line,
  health checks included.
- Mappings live only in process memory while a request is served. Keep that memory off disk:
  disable swap on the host or encrypt it, and disable core dumps.

## Sizing

Measured on an Apple M5 Pro (CPU only), version 0.1.0:

| | Without ML | With ML |
|---|---|---|
| Image size, uncompressed | about 200 MB | about 2.3 GB (1.1 GB of it the model) |
| Resident memory, idle | about 40 MB | about 1.7 GB (1.5 GB the model) |
| Start-up | under 1 s | about 8 s, model cached in the image |
| Detection cost | well under 1 ms for a short message | about 140 ms per 200 words, 2.6 s per 3,000 words |

The proxy is asynchronous. Detection runs in a worker thread, and the ML detector runs one
inference at a time per process, so under load requests wait for each other's ML detection. A
cache hit (a conversation resending its history) costs about 0.3 ms. Scale out with more
replicas; each replica loads its own copy of the model. The leak audit of a conversation of
100 messages and 200 entities takes about 2 ms.

The provider's own latency dominates in practice. Watch
`aliasgate_request_overhead_seconds` (section 10 of [usage.md](usage.md)) to see what the gateway
adds.

## Monitoring

| Metric | Type | Labels |
|---|---|---|
| `aliasgate_requests_total` | counter | `route`, `status_class`, `outcome` |
| `aliasgate_entities_total` | counter | `entity_type`, `source` |
| `aliasgate_unresolved_placeholders_total` | counter | `route` |
| `aliasgate_leak_audit_failures_total` | counter | `route`, `entity_type` |
| `aliasgate_rejections_total` | counter | `route`, `code` |
| `aliasgate_detection_seconds` | histogram | `detector` |
| `aliasgate_request_overhead_seconds` | histogram | `route`, `phase` |
| `aliasgate_detection_cache_total` | counter | `result` |

Suggested alerts:

- `increase(aliasgate_leak_audit_failures_total[5m]) > 0`: a detected value was about to leave.
  Nothing was sent, but a client is putting data in a field the gateway does not pseudonymise.
- `aliasgate_rejections_total` rising, by `code`: `unsupported_content` means users send
  attachments; `pseudonymization_failed` and `internal_error` mean the gateway is failing.
- `aliasgate_unresolved_placeholders_total` rising: a model has started altering placeholders,
  and users see them.
- `aliasgate_requests_total{outcome="upstream_error"}` rising: the provider or the egress path
  is failing.

## Deployment checklist

- [ ] The gateway runs inside the trust boundary, behind an ingress that terminates TLS and
      authenticates clients.
- [ ] Direct access to the provider domains is blocked for the clients; only the gateway may
      reach them.
- [ ] The configuration and dictionaries are mounted read-only and kept out of version control.
- [ ] `aliasgate check` passes on the configuration you deploy.
- [ ] Dictionaries list your customers, projects and codenames, and the allow list holds your
      own names; you tried them with `aliasgate demo`.
- [ ] `policy.unsupported_content` is `block`, unless you decided otherwise in writing.
- [ ] `policy.leak_audit` is on.
- [ ] `/metrics` is reachable only from your monitoring, and the alerts above exist.
- [ ] Swap and core dumps are off or encrypted on the gateway hosts.
- [ ] The container runs as non-root with a read-only file system.
- [ ] You ran the evaluation on your own samples:
      `python -m aliasgate.eval --config <file> <corpus.jsonl>...`. [evaluation.md](evaluation.md)
      explains the numbers and how to handle real samples. `aliasgate.eval` ships with the
      package; the synthetic corpora and their generator do not.
- [ ] You reviewed the [threat model](threat-model.md) and its residual risks with your data
      protection officer.

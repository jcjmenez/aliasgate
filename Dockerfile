# syntax=docker/dockerfile:1
#
# aliasgate gateway image.
#
#   docker build -t aliasgate .                          # rules and dictionary
#   docker build -t aliasgate:ml --build-arg EXTRAS=ml . # plus the GLiNER model, baked in
#
# No configuration or dictionary is baked in: mount them at /etc/aliasgate. Base images are
# pinned by digest; update the tag and the digest together.

FROM ghcr.io/astral-sh/uv:0.11.26@sha256:3d868e555f8f1dbc324afa005066cd11e1053fc4743b9808ca8025283e65efa5 AS uv

FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/aliasgate
# Empty for the core image; "ml" adds the ML extra.
ARG EXTRAS=""
WORKDIR /src
# Dependencies first, from the lock file only, so that a source change does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project ${EXTRAS:+--extra "$EXTRAS"}
COPY README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable ${EXTRAS:+--extra "$EXTRAS"}

# The ML image downloads the pinned model at build time, together with the tokenizer of its
# base encoder (microsoft/mdeberta-v3-base, fetched at its default branch), then proves that
# the detector builds from the cache alone, as the container will at start-up.
ENV HF_HOME=/opt/huggingface HF_HUB_DISABLE_TELEMETRY=1
RUN mkdir -p "$HF_HOME" && if [ "$EXTRAS" = "ml" ]; then \
        /opt/aliasgate/bin/python -c 'from aliasgate.config import MLSettings; \
from aliasgate.detectors.gliner import load_gliner; \
ml = MLSettings(); load_gliner(ml.model, ml.revision)' \
        && HF_HUB_OFFLINE=1 /opt/aliasgate/bin/python -c 'from aliasgate.config import Settings; \
from aliasgate.factory import build_pseudonymizer; \
build_pseudonymizer(Settings.model_validate({"detection": {"ml": {"enabled": True}}}))'; \
    fi \
    # huggingface_hub writes some cache files as 0600; the gateway's user must read them all.
    && chmod -R a+rX "$HF_HOME"

FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
LABEL org.opencontainers.image.title="aliasgate" \
      org.opencontainers.image.description="Reversible pseudonymisation gateway for LLM APIs" \
      org.opencontainers.image.source="https://github.com/jcjmenez/aliasgate" \
      org.opencontainers.image.licenses="Apache-2.0"
RUN useradd --system --uid 10001 --user-group --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin aliasgate
# Owned by root and read-only for the gateway's user.
COPY --from=build /opt/aliasgate /opt/aliasgate
COPY --from=build /opt/huggingface /opt/huggingface
ENV PATH=/opt/aliasgate/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/huggingface \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1
USER 10001:10001
EXPOSE 8080
# http.client ignores proxy variables, so the probe always reaches the gateway itself.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD ["python", "-c", "import http.client, sys; c = http.client.HTTPConnection('127.0.0.1', 8080, timeout=4); c.request('GET', '/healthz'); sys.exit(c.getresponse().status != 200)"]
ENTRYPOINT ["aliasgate"]
CMD ["serve", "--config", "/etc/aliasgate/aliasgate.yaml", "--host", "0.0.0.0"]

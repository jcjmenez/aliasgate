"""Proxy fixtures: a gateway built from real settings, called in process, with a mocked upstream."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import respx
import yaml
from payloads import OPENAI, ClientFactory

from aliasgate.config import Settings, load_settings
from aliasgate.engine import Pseudonymizer
from aliasgate.proxy.app import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Default settings plus a dictionary with Juan Pérez (PERSON) and ACME Corporation (ORG)."""
    (tmp_path / "dictionary.yaml").write_text(
        yaml.safe_dump(
            {
                "terms": [
                    {"term": "Juan Pérez", "type": "PERSON"},
                    {"term": "ACME Corporation", "type": "ORG"},
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    config = {
        "detection": {"dictionary": {"files": ["dictionary.yaml"]}},
        "upstreams": {"passthrough_paths": ["openai GET /v1/models"]},
    }
    path = tmp_path / "aliasgate.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return load_settings(path, env={})


@pytest.fixture
def upstream() -> Iterator[respx.MockRouter]:
    """The mocked OpenAI API. Any request that matches no route fails the test."""
    with respx.mock(base_url=OPENAI, assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def make_client(settings: Settings, upstream: respx.MockRouter) -> ClientFactory:
    """Build a gateway (optionally with other settings or another pseudonymizer) and a client."""

    @asynccontextmanager
    async def factory(
        custom: Settings | None = None, *, pseudonymizer: Pseudonymizer | None = None
    ) -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient() as http_client:
            app = create_app(
                custom or settings, pseudonymizer=pseudonymizer, http_client=http_client
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://gateway.test"
            ) as client:
                yield client

    return factory


@pytest.fixture
async def client(make_client: ClientFactory) -> AsyncIterator[httpx.AsyncClient]:
    """A client of the gateway built with the default test settings."""
    async with make_client() as gateway_client:
        yield gateway_client

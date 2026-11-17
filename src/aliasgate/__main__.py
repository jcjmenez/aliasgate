"""`python -m aliasgate --config path.yaml` serves the gateway. The full CLI arrives in slice 12."""

import argparse
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from aliasgate.config import load_settings
from aliasgate.proxy.app import configure_logging, create_app


def main(argv: Sequence[str] | None = None) -> None:
    """Load the settings, log JSON lines and serve the gateway until interrupted."""
    parser = argparse.ArgumentParser(
        prog="aliasgate", description="Reversible pseudonymisation gateway for LLM APIs."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="YAML configuration file; ALIASGATE_* environment variables override it",
    )
    args = parser.parse_args(argv)
    settings = load_settings(args.config)
    configure_logging(settings.logging.level)
    # The gateway logs one line per request itself; uvicorn's access log would add the path
    # and query string of every request.
    uvicorn.run(
        create_app(settings),
        host=settings.listen.host,
        port=settings.listen.port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()

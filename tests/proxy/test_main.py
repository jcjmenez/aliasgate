import os
import runpy
import sys
import warnings
from pathlib import Path
from typing import Any

import pytest
import uvicorn
import yaml
from starlette.applications import Starlette

from aliasgate import __main__ as main_module
from aliasgate.proxy import app as app_module


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Record uvicorn.run and configure_logging instead of serving or touching global logging."""
    recorded: dict[str, Any] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        recorded["app"] = app
        recorded["run"] = kwargs

    def fake_configure_logging(level: str) -> None:
        recorded["level"] = level

    monkeypatch.setattr(uvicorn, "run", fake_run)
    # Patched in both places: runpy re-imports the name from the app module.
    monkeypatch.setattr(app_module, "configure_logging", fake_configure_logging)
    monkeypatch.setattr(main_module, "configure_logging", fake_configure_logging)
    for name in list(os.environ):
        if name.upper().startswith("ALIASGATE_"):
            monkeypatch.delenv(name)
    return recorded


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "aliasgate.yaml"
    config = {"listen": {"host": "127.0.0.1", "port": 9123}, "logging": {"level": "DEBUG"}}
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_main_loads_the_settings_and_serves_the_app(calls: dict[str, Any], tmp_path: Path) -> None:
    main_module.main(["--config", str(_config(tmp_path))])
    assert isinstance(calls["app"], Starlette)
    assert calls["run"] == {
        "host": "127.0.0.1",
        "port": 9123,
        "log_config": None,
        "access_log": False,
    }
    assert calls["level"] == "DEBUG"


def test_main_without_a_config_file_uses_the_defaults(calls: dict[str, Any]) -> None:
    main_module.main([])
    assert calls["run"]["host"] == "127.0.0.1"
    assert calls["run"]["port"] == 8080
    assert calls["level"] == "INFO"


def test_running_the_package_as_a_module_calls_main(
    calls: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["aliasgate", "--config", str(_config(tmp_path))])
    with warnings.catch_warnings():
        # runpy warns that aliasgate.__main__ is already imported; that is expected here.
        warnings.simplefilter("ignore", RuntimeWarning)
        runpy.run_module("aliasgate", run_name="__main__")
    assert calls["run"]["port"] == 9123

"""The documentation and packaging files agree with the code they describe."""

import ast
import inspect
import py_compile
import re
from pathlib import Path

import yaml
from prometheus_client.parser import text_string_to_metric_families

from aliasgate.metrics import GatewayMetrics
from aliasgate.proxy import errors
from aliasgate.proxy.errors import GatewayError

ROOT = Path(__file__).parents[1]
USAGE = ROOT / "docs" / "usage.md"
DEPLOYMENT = ROOT / "docs" / "deployment.md"
ERROR_ROW = re.compile(r"^\| `([a-z_]+)` \| (\d{3}) \| `([a-z_]+)` \|", re.MULTILINE)
METRIC_ROW = re.compile(r"^\| `(aliasgate_[a-z_]+)` \| (counter|histogram) \|", re.MULTILINE)


def _section(path: Path, heading: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(f"\n## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else None]


def _gateway_errors() -> dict[str, tuple[int, str]]:
    """Every error the gateway can produce, by code: (status, outcome)."""
    found: dict[str, tuple[int, str]] = {}
    for name, function in inspect.getmembers(errors, inspect.isfunction):
        if function.__module__ != errors.__name__ or name.startswith("_"):
            continue
        if inspect.signature(function).return_annotation not in (GatewayError, "GatewayError"):
            continue
        arguments = ["x"] * len(inspect.signature(function).parameters)
        error = function(*arguments)
        found[error.code] = (error.status, error.outcome)
    return found


def test_the_error_code_table_matches_the_errors_the_gateway_produces() -> None:
    documented = {
        code: (int(status), outcome)
        for code, status, outcome in ERROR_ROW.findall(_section(USAGE, "8. Error codes"))
    }
    assert documented == _gateway_errors()


def test_every_gateway_error_is_built_by_a_function_of_the_errors_module() -> None:
    # Codes built elsewhere would be missing from the comparison above.
    tree = ast.parse((ROOT / "src/aliasgate/proxy/app.py").read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "GatewayError"
    ]
    assert calls == []
    assert len(_gateway_errors()) == 11


def _exposed_metrics() -> dict[str, str]:
    metrics = GatewayMetrics([lambda: (0, 0)])
    families = text_string_to_metric_families(metrics.render().decode("utf-8"))
    return {
        family.name + ("_total" if family.type == "counter" else ""): family.type
        for family in families
    }


def test_the_metric_tables_list_exactly_the_metrics_the_gateway_exposes() -> None:
    exposed = _exposed_metrics()
    for path, heading in ((USAGE, "10. Logs, headers and metrics"), (DEPLOYMENT, "Monitoring")):
        documented = dict(METRIC_ROW.findall(_section(path, heading)))
        assert documented == exposed, path.name


def test_every_from_line_of_the_dockerfile_pins_its_image_by_digest() -> None:
    lines = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
    images = [line.split()[1] for line in lines if line.startswith("FROM ")]
    assert len(images) == 3
    for image in images:
        assert re.fullmatch(r"[a-z0-9./_-]+:[\w.-]+@sha256:[0-9a-f]{64}", image), image


def test_the_image_runs_as_a_non_root_user_and_bakes_in_no_configuration() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    users = re.findall(r"^USER (\S+)$", dockerfile, re.MULTILINE)
    assert users == ["10001:10001"]
    copied = re.findall(r"^COPY (?:--from=\S+ )?(.+) \S+$", dockerfile, re.MULTILINE)
    assert not [source for source in copied if "yaml" in source and "uv.lock" not in source]
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ignored[ignored.index("*") + 1 :][:5] == [
        "!pyproject.toml",
        "!uv.lock",
        "!README.md",
        "!LICENSE",
        "!src/",
    ]


def test_the_release_workflow_uses_trusted_publishing_and_no_stored_secret() -> None:
    text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "secrets." not in text
    workflow = yaml.safe_load(text)
    assert workflow["permissions"] == {}
    jobs = workflow["jobs"]
    assert jobs["pypi"]["permissions"] == {"id-token": "write"}
    assert jobs["pypi"]["environment"]["name"] == "pypi"
    publish = [step for step in jobs["pypi"]["steps"] if "pypi-publish" in step.get("uses", "")]
    assert len(publish) == 1
    assert "with" not in publish[0]  # no password or token input
    assert jobs["ci"]["uses"] == "./.github/workflows/ci.yml"
    for name, job in jobs.items():
        for step in job.get("steps", []):
            uses = step.get("uses", "")
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", uses) or not uses, (name, uses)


def test_the_ci_workflow_can_be_called_by_the_release_workflow() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    # PyYAML reads the key `on` as the boolean True.
    assert "workflow_call" in workflow[True]


def test_the_example_clients_are_valid_python(tmp_path: Path) -> None:
    for name in ("openai_client.py", "anthropic_client.py"):
        py_compile.compile(str(ROOT / "examples" / name), cfile=str(tmp_path / name), doraise=True)


def test_the_compose_example_mounts_the_example_configuration_read_only() -> None:
    compose = yaml.safe_load((ROOT / "examples/docker-compose.yaml").read_text(encoding="utf-8"))
    service = compose["services"]["aliasgate"]
    assert service["ports"] == ["127.0.0.1:8080:8080"]
    assert service["read_only"] is True
    for volume in service["volumes"]:
        source, target, mode = volume.split(":")
        assert (ROOT / "examples" / source).is_file()
        assert target.startswith("/etc/aliasgate/")
        assert mode == "ro"

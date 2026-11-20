"""`python -m aliasgate.eval`: options, exit codes, and output that never contains text."""

import json
import os
import runpy
import sys
import warnings
from pathlib import Path

import pytest

from aliasgate.engine import Pseudonymizer
from aliasgate.eval import __main__ as command
from fakes import FailingDetector

MARKER = "Confidential"
# "Confidential mail juan.perez@example.com and Alice Johnson": the rules find the email (18..40)
# and miss the name (45..58); with the second example, 22 of 44 gold characters leak.
FIRST: dict[str, object] = {
    "text": f"{MARKER} mail juan.perez@example.com and Alice Johnson",
    "spans": [
        {"start": 18, "end": 40, "type": "EMAIL"},
        {"start": 45, "end": 58, "type": "PERSON"},
    ],
}
SECOND: dict[str, object] = {
    "text": f"{MARKER} met Bob Smith",
    "spans": [{"start": 17, "end": 26, "type": "PERSON"}],
}
THIRD: dict[str, object] = {"text": f"{MARKER} order 4521", "spans": []}
WORDS = ("Confidential", "juan", "Alice", "Bob")


def _corpus(tmp_path: Path, lang: str = "en", name: str | None = None) -> Path:
    path = tmp_path / (name or f"{lang}.jsonl")
    lines = [
        json.dumps({"id": f"{lang}-{number}", "lang": lang, **example})
        for number, example in enumerate((FIRST, SECOND, THIRD), start=1)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _run(
    capsys: pytest.CaptureFixture[str], *argv: str, env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    code = command.main(list(argv), env=env or {})
    out, err = capsys.readouterr()
    for word in WORDS:
        assert word not in out
        assert word not in err
    return code, out, err


def test_the_table_has_numbers_and_entity_types_but_no_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = _run(capsys, str(_corpus(tmp_path)))
    assert code == 0
    assert "en: 3 examples, leak rate 0.5000 (22 of 44 characters), round-trip failures 0" in out
    assert "examples without entities: 1, with a predicted span: 0 (0.0%)" in out
    assert "EMAIL" in out
    assert "PERSON" in out
    assert "missed" not in out


def test_the_json_report_has_every_number(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = _run(capsys, str(_corpus(tmp_path)), "--format", "json")
    assert code == 0
    report = json.loads(out)["en"]
    assert report["examples"] == 3
    assert (report["leaked_chars"], report["gold_chars"]) == (22, 44)
    assert report["leak_rate"] == 0.5
    assert (report["examples_without_entities"], report["flagged_without_entities"]) == (1, 0)
    assert report["round_trip_failures"] == 0
    assert report["missed_spans"] == 2
    assert report["misses"] == []
    assert report["by_type"] == [
        {
            "entity_type": "EMAIL",
            "gold": 1,
            "predicted": 1,
            "exact_tp": 1,
            "overlap_tp": 1,
            "recall_exact": 1.0,
            "recall_overlap": 1.0,
            "precision_exact": 1.0,
        },
        {
            "entity_type": "PERSON",
            "gold": 2,
            "predicted": 0,
            "exact_tp": 0,
            "overlap_tp": 0,
            "recall_exact": 0.0,
            "recall_overlap": 0.0,
            "precision_exact": 1.0,
        },
    ]


@pytest.mark.parametrize(("limit", "code"), [("0.7", 0), ("0.5", 0), ("0.4999", 1), ("0", 1)])
def test_max_leak_rate_sets_the_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], limit: str, code: int
) -> None:
    assert _run(capsys, str(_corpus(tmp_path)), "--max-leak-rate", limit)[0] == code


def test_types_restrict_the_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(capsys, str(_corpus(tmp_path)), "--types", "EMAIL", "--format", "json")
    report = json.loads(out)["en"]
    assert [score["entity_type"] for score in report["by_type"]] == ["EMAIL"]
    assert (code, report["leak_rate"], report["examples_without_entities"]) == (0, 0.0, 2)


def test_show_misses_lists_ids_offsets_and_types_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = str(_corpus(tmp_path))
    _, out, _ = _run(capsys, corpus, "--show-misses", "1")
    assert "missed spans (1 of 2):\n  en-1 45..58 PERSON, 13 characters not covered" in out
    assert "en-2" not in out
    _, out, _ = _run(capsys, corpus, "--show-misses", "5", "--format", "json")
    assert json.loads(out)["en"]["misses"] == [
        {"example_id": "en-1", "start": 45, "end": 58, "entity_type": "PERSON", "leaked_chars": 13},
        {"example_id": "en-2", "start": 17, "end": 26, "entity_type": "PERSON", "leaked_chars": 9},
    ]


def test_examples_are_reported_per_language(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    es, en = _corpus(tmp_path, "es"), _corpus(tmp_path, "en")
    _, out, _ = _run(capsys, str(es), str(en))
    assert out.index("en: 3 examples") < out.index("es: 3 examples")
    _, out, _ = _run(capsys, str(es), str(en), "--format", "json")
    assert list(json.loads(out)) == ["en", "es"]


def test_the_configuration_file_and_the_environment_are_used(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = str(_corpus(tmp_path))
    config = tmp_path / "aliasgate.yaml"
    config.write_text("detection:\n  rules:\n    enabled_types: [PHONE]\n", encoding="utf-8")
    _, out, _ = _run(capsys, corpus, "--config", str(config), "--format", "json")
    assert json.loads(out)["en"]["leaked_chars"] == 44
    env = {"ALIASGATE_DETECTION__RULES__ENABLED_TYPES": '["PHONE"]'}
    _, out, _ = _run(capsys, corpus, "--format", "json", env=env)
    assert json.loads(out)["en"]["leaked_chars"] == 44


def test_an_invalid_corpus_exits_2_with_its_position(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "en-9",
                "lang": "en",
                **FIRST,
                "spans": [{"start": 0, "end": 999, "type": "PERSON"}],
            }
        ),
        encoding="utf-8",
    )
    code, _, err = _run(capsys, str(path))
    assert code == 2
    assert "line 1, example 'en-9'" in err


def test_duplicate_ids_across_files_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first, second = _corpus(tmp_path, name="a.jsonl"), _corpus(tmp_path, name="b.jsonl")
    code, _, err = _run(capsys, str(first), str(second))
    assert code == 2
    assert "example id 'en-1' appears in more than one place" in err


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("detection:\n  unknown_key: 1\n", "invalid configuration"),
        (f"detection: [{MARKER}\n", "the configuration file is not valid YAML"),
    ],
)
def test_an_invalid_configuration_exits_2_without_its_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str, message: str
) -> None:
    config = tmp_path / "aliasgate.yaml"
    config.write_text(content, encoding="utf-8")
    code, _, err = _run(capsys, str(_corpus(tmp_path)), "--config", str(config))
    assert code == 2
    assert message in err


def test_a_missing_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = _run(capsys, str(tmp_path / "missing.jsonl"))
    assert code == 2
    assert "missing.jsonl" in err


def test_a_failing_detector_exits_2_with_its_name_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        command, "build_pseudonymizer", lambda _: Pseudonymizer([FailingDetector()])
    )
    code, _, err = _run(capsys, str(_corpus(tmp_path)))
    assert code == 2
    assert "'failing'" in err


def test_an_unexpected_error_exits_2_with_its_class_name_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError(f"{MARKER} juan Alice Bob")

    monkeypatch.setattr(command, "evaluate", explode)
    code, _, err = _run(capsys, str(_corpus(tmp_path)))
    assert code == 2
    assert "RuntimeError" in err


@pytest.mark.parametrize(
    "option",
    [
        ["--types", "email"],
        ["--types", ""],
        ["--max-leak-rate", "1.5"],
        ["--max-leak-rate", "nan"],
        ["--show-misses", "-1"],
        ["--format", "csv"],
    ],
)
def test_invalid_options_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], option: list[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        command.main([str(_corpus(tmp_path)), *option], env={})
    assert caught.value.code == 2


def test_running_the_package_as_a_module_exits_with_the_code_of_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in list(os.environ):
        if name.upper().startswith("ALIASGATE_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr(
        sys, "argv", ["aliasgate.eval", str(_corpus(tmp_path)), "--max-leak-rate", "0"]
    )
    with warnings.catch_warnings():
        # runpy warns that aliasgate.eval.__main__ is already imported; that is expected here.
        warnings.simplefilter("ignore", RuntimeWarning)
        with pytest.raises(SystemExit) as caught:
            runpy.run_module("aliasgate.eval", run_name="__main__")
    assert caught.value.code == 1
    assert "en: 3 examples" in capsys.readouterr().out

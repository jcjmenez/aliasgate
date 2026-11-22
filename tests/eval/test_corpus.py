import ipaddress
import json
from pathlib import Path

import phonenumbers
import pytest

import generate_corpus
from aliasgate.detectors.checksums import (
    es_cif_valid,
    es_dni_valid,
    es_nie_valid,
    iban_valid,
    luhn_valid,
    us_ssn_plausible,
)
from aliasgate.eval.corpus import CorpusError, Example, GoldSpan, load_corpus
from aliasgate.mapping import normalize_key

CORPUS = Path(__file__).parent / "corpus"
LANGUAGES = ("en", "es")
SYNTHETIC_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "192.0.2.0/24",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "10.0.0.0/8",
        "192.168.0.0/16",
        "2001:db8::/32",
    )
)
CHECKSUMS = {
    "IBAN": (iban_valid,),
    "CREDIT_CARD": (luhn_valid,),
    "NATIONAL_ID": (es_dni_valid, es_nie_valid, us_ssn_plausible),
    "TAX_ID": (es_cif_valid,),
}


@pytest.fixture(scope="module", params=LANGUAGES)
def corpus(request: pytest.FixtureRequest) -> tuple[str, list[Example]]:
    lang: str = request.param
    return lang, load_corpus(CORPUS / f"synthetic.{lang}.jsonl")


def _surfaces(example: Example) -> list[tuple[str, str]]:
    return [(span.entity_type, example.text[span.start : span.end]) for span in example.spans]


def _repeats_in_another_case(example: Example) -> bool:
    surfaces = [surface for _, surface in _surfaces(example)]
    return any(
        first != second and normalize_key(first) == normalize_key(second)
        for index, first in enumerate(surfaces)
        for second in surfaces[index + 1 :]
    )


# The committed corpora ------------------------------------------------------------------------


def test_each_committed_corpus_has_500_examples_of_its_language(
    corpus: tuple[str, list[Example]],
) -> None:
    lang, examples = corpus
    assert len(examples) == 500
    assert {example.lang for example in examples} == {lang}
    assert [example.id for example in examples] == [f"{lang}-{n:04d}" for n in range(1, 501)]


def test_at_least_15_percent_of_examples_have_no_entity(
    corpus: tuple[str, list[Example]],
) -> None:
    _, examples = corpus
    assert sum(1 for example in examples if not example.spans) >= 0.15 * len(examples)


def test_at_least_10_percent_of_examples_repeat_an_entity_in_another_letter_case(
    corpus: tuple[str, list[Example]],
) -> None:
    _, examples = corpus
    assert sum(1 for example in examples if _repeats_in_another_case(example)) >= 0.10 * len(
        examples
    )


def test_every_entity_type_of_the_generator_occurs(corpus: tuple[str, list[Example]]) -> None:
    _, examples = corpus
    found = {span.entity_type for example in examples for span in example.spans}
    assert found == set(generate_corpus.GOLD_TYPES.values())


def test_every_gold_span_is_non_empty_and_has_no_surrounding_whitespace(
    corpus: tuple[str, list[Example]],
) -> None:
    _, examples = corpus
    for example in examples:
        for _, surface in _surfaces(example):
            assert surface, example.id
            assert surface == surface.strip(), example.id


def test_every_identifier_with_a_checksum_validates(corpus: tuple[str, list[Example]]) -> None:
    _, examples = corpus
    for example in examples:
        for entity_type, surface in _surfaces(example):
            if entity_type in CHECKSUMS:
                assert any(check(surface) for check in CHECKSUMS[entity_type]), example.id


def test_emails_ip_addresses_and_phones_come_from_ranges_reserved_for_examples(
    corpus: tuple[str, list[Example]],
) -> None:
    _, examples = corpus
    for example in examples:
        for entity_type, surface in _surfaces(example):
            if entity_type == "EMAIL":
                assert surface.lower().endswith(("example.com", "example.org")), example.id
            elif entity_type == "IP_ADDRESS":
                address = ipaddress.ip_address(surface)
                assert any(address in network for network in SYNTHETIC_NETWORKS), example.id
            elif entity_type == "PHONE":
                number = phonenumbers.parse(surface, "ES" if example.lang == "es" else "US")
                national = phonenumbers.national_significant_number(number)
                if number.country_code == 1:  # 555-0100 to 555-0199: reserved for fiction
                    assert national[3:8] == "55501", example.id
                elif number.country_code == 44:  # Ofcom's drama range 020 7946 0xxx
                    assert national.startswith("2079460"), example.id
                else:
                    assert number.country_code == 34, example.id


# The generator --------------------------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGUAGES)
def test_the_generator_has_at_least_40_entity_templates_per_language(lang: str) -> None:
    entity_templates, empty_templates = generate_corpus.TEMPLATES[lang]
    assert len(entity_templates) >= 40
    assert empty_templates
    for template in empty_templates:
        assert generate_corpus.render(template, generate_corpus.values_for(lang))[1] == []


@pytest.mark.parametrize("lang", LANGUAGES)
def test_the_generator_is_deterministic(lang: str) -> None:
    assert generate_corpus.generate(lang) == generate_corpus.generate(lang)


def test_check_reports_whether_the_files_differ_from_a_fresh_generation(tmp_path: Path) -> None:
    assert generate_corpus.main(["--out", str(tmp_path)]) == 0
    assert generate_corpus.main(["--check", "--out", str(tmp_path)]) == 0
    first = tmp_path / "synthetic.en.jsonl"
    first.write_text(first.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert generate_corpus.main(["--check", "--out", str(tmp_path)]) == 1
    first.unlink()
    assert generate_corpus.main(["--check", "--out", str(tmp_path)]) == 1


def test_templates_fill_case_variants_and_escaped_braces() -> None:
    values = generate_corpus.values_for("en")
    text, spans = generate_corpus.render('{{"a": "{org|lower}", "b": "{org}"}}', values)
    first, second = (text[start:end] for start, end, _ in spans)
    assert text.startswith('{"a": "')
    assert text.endswith('"}')
    assert first == second.lower() != second
    assert [entity_type for _, _, entity_type in spans] == ["ORG", "ORG"]


# load_corpus ----------------------------------------------------------------------------------

TEXT = "Secret Alice Johnson text"


def _write(tmp_path: Path, *lines: object) -> Path:
    path = tmp_path / "corpus.jsonl"
    path.write_text(
        "".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines),
        encoding="utf-8",
    )
    return path


def _line(**overrides: object) -> dict[str, object]:
    line: dict[str, object] = {
        "id": "case-7",
        "lang": "en",
        "text": TEXT,
        "spans": [{"start": 7, "end": 20, "type": "PERSON"}],
    }
    line.update(overrides)
    return line


def test_a_valid_file_loads_and_blank_lines_are_skipped(tmp_path: Path) -> None:
    path = _write(tmp_path, _line(), "", _line(id="case-8", spans=[]))
    assert load_corpus(path) == [
        Example("case-7", "en", TEXT, (GoldSpan(7, 20, "PERSON"),)),
        Example("case-8", "en", TEXT, ()),
    ]


def test_an_out_of_range_span_fails_with_the_example_id_and_without_text(tmp_path: Path) -> None:
    path = _write(tmp_path, _line(spans=[{"start": 7, "end": 99, "type": "PERSON"}]))
    with pytest.raises(CorpusError) as caught:
        load_corpus(path)
    message = str(caught.value)
    assert "line 1, example 'case-7': span 1 is out of range" in message
    assert "Alice" not in message
    assert "Secret" not in message


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("not json {", "not a JSON object"),
        ([1, 2], "not a JSON object"),
        (_line(extra=1), "id, lang, text and spans"),
        ({"id": "case-7", "lang": "en", "text": TEXT}, "id, lang, text and spans"),
        (_line(id=""), "'id' must be a non-empty string"),
        (_line(id=7), "'id' must be a non-empty string"),
        (_line(lang=""), "'lang' must be a non-empty string"),
        (_line(text=None), "'text' must be a string"),
        (_line(spans={}), "'spans' must be a list"),
        (_line(spans=[[7, 20]]), "span 1 must be an object with start, end and type"),
        (
            _line(spans=[{"start": 7, "end": 20}]),
            "span 1 must be an object with start, end and type",
        ),
        (
            _line(spans=[{"start": "7", "end": 20, "type": "PERSON"}]),
            "span 1 offsets must be integers",
        ),
        (
            _line(spans=[{"start": True, "end": 20, "type": "PERSON"}]),
            "span 1 offsets must be integers",
        ),
        (_line(spans=[{"start": -1, "end": 20, "type": "PERSON"}]), "span 1 is out of range"),
        (_line(spans=[{"start": 7, "end": 7, "type": "PERSON"}]), "span 1 is out of range"),
        (
            _line(spans=[{"start": 7, "end": 20, "type": "person"}]),
            "span 1 has an invalid entity type",
        ),
        (_line(spans=[{"start": 7, "end": 20, "type": 5}]), "span 1 has an invalid entity type"),
        (
            _line(
                spans=[
                    {"start": 7, "end": 12, "type": "PERSON"},
                    {"start": 10, "end": 20, "type": "PERSON"},
                ]
            ),
            "span 2 overlaps or precedes span 1",
        ),
        (
            _line(
                spans=[
                    {"start": 13, "end": 20, "type": "PERSON"},
                    {"start": 7, "end": 12, "type": "PERSON"},
                ]
            ),
            "span 2 overlaps or precedes span 1",
        ),
    ],
)
def test_malformed_lines_fail_with_their_position_and_without_text(
    tmp_path: Path, line: object, reason: str
) -> None:
    with pytest.raises(CorpusError, match="line 1") as caught:
        load_corpus(_write(tmp_path, line))
    assert reason in str(caught.value)
    assert "Alice" not in str(caught.value)


def test_a_duplicate_id_fails_on_its_second_line(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match=r"line 2, example 'case-7': duplicate id"):
        load_corpus(_write(tmp_path, _line(), _line()))


def test_the_committed_corpora_load_into_the_same_spans_without_entity_ids(
    corpus: tuple[str, list[Example]],
) -> None:
    lang, examples = corpus
    lines = (CORPUS / f"synthetic.{lang}.jsonl").read_text(encoding="utf-8").splitlines()
    assert [example.spans for example in examples] == [
        tuple(
            GoldSpan(span["start"], span["end"], span["type"]) for span in json.loads(line)["spans"]
        )
        for line in lines
    ]
    assert all(span.entity is None for example in examples for span in example.spans)


# Entity ids -----------------------------------------------------------------------------------


def test_a_span_with_an_entity_id_loads_it_and_a_span_without_one_has_none(
    tmp_path: Path,
) -> None:
    spans = [
        {"start": 7, "end": 12, "type": "PERSON", "entity": "p1"},
        {"start": 13, "end": 20, "type": "PERSON"},
    ]
    (example,) = load_corpus(_write(tmp_path, _line(spans=spans)))
    assert example.spans == (GoldSpan(7, 12, "PERSON", "p1"), GoldSpan(13, 20, "PERSON"))
    assert example.spans[1].entity is None


@pytest.mark.parametrize("entity", ["x" * 64, "p1", "A.b-c_9", "0"])
def test_entity_ids_of_1_to_64_ascii_letters_digits_and_separators_are_accepted(
    tmp_path: Path, entity: str
) -> None:
    spans = [{"start": 7, "end": 20, "type": "PERSON", "entity": entity}]
    (example,) = load_corpus(_write(tmp_path, _line(spans=spans)))
    assert example.spans[0].entity == entity


@pytest.mark.parametrize(
    "entity", ["", "x" * 65, "Alice Johnson", "Lucía", "p1\n", 12345, None, True, ["p1"]]
)
def test_an_invalid_entity_id_is_rejected_without_repeating_it(
    tmp_path: Path, entity: object
) -> None:
    spans = [
        {"start": 7, "end": 12, "type": "PERSON"},
        {"start": 13, "end": 20, "type": "PERSON", "entity": entity},
    ]
    with pytest.raises(CorpusError) as caught:
        load_corpus(_write(tmp_path, _line(spans=spans)))
    message = str(caught.value)
    assert "line 1, example 'case-7': span 2 has an invalid entity id" in message
    if entity:
        assert str(entity).strip() not in message
        assert json.dumps(entity) not in message


def test_spans_sharing_an_entity_id_with_another_type_are_rejected_by_number(
    tmp_path: Path,
) -> None:
    spans = [
        {"start": 0, "end": 6, "type": "ORG", "entity": "who.alice"},
        {"start": 7, "end": 12, "type": "PERSON", "entity": "who.bob"},
        {"start": 13, "end": 20, "type": "PERSON", "entity": "who.alice"},
    ]
    with pytest.raises(CorpusError) as caught:
        load_corpus(_write(tmp_path, _line(spans=spans)))
    message = str(caught.value)
    assert "span 3 has the entity id of span 1 with another type" in message
    assert "who." not in message


def test_spans_sharing_an_entity_id_and_its_type_are_one_entity(tmp_path: Path) -> None:
    spans = [
        {"start": 0, "end": 6, "type": "PERSON", "entity": "e"},
        {"start": 7, "end": 12, "type": "ORG", "entity": "f"},
        {"start": 13, "end": 20, "type": "PERSON", "entity": "e"},
    ]
    (example,) = load_corpus(_write(tmp_path, _line(spans=spans)))
    assert [span.entity for span in example.spans] == ["e", "f", "e"]


@pytest.mark.parametrize(
    "span",
    [
        {"start": 7, "end": 20, "type": "PERSON", "id": "p1"},
        {"start": 7, "end": 20, "type": "PERSON", "entity": "p1", "extra": 1},
        {"start": 7, "end": 20, "entity": "p1"},
    ],
)
def test_an_unknown_or_missing_span_key_is_rejected_with_the_optional_key_named(
    tmp_path: Path, span: dict[str, object]
) -> None:
    with pytest.raises(CorpusError) as caught:
        load_corpus(_write(tmp_path, _line(spans=[span])))
    assert "span 1 must be an object with start, end and type, and optionally entity" in str(
        caught.value
    )

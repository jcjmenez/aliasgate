"""The person mentions corpus (slices 15 and 17): its generator, its files and their shape."""

import hashlib
import random
import re
import unicodedata
from pathlib import Path

import pytest

import generate_person_corpus as generator
from aliasgate.config import load_settings, load_terms
from aliasgate.eval.corpus import Example, GoldSpan, load_corpus
from aliasgate.eval.metrics import Report, evaluate
from aliasgate.factory import build_pseudonymizer
from aliasgate.mapping import normalize_key

CORPUS = Path(__file__).parent / "corpus"
LANGUAGES = ("en", "es")
CATEGORIES = (
    "full",
    "first",
    "last",
    "title",
    "before",
    "run",
    "possessive",
    "shared-last",
    "shared-first",
    "other",
    "word",
    "sentence",
    "lower",
    "org",
    "single",
    "hyphen",
    "don",
)
# SHA-256 of the first 280 lines of each file and of the first 719 entries of the dictionary,
# computed before slice 17 changed the generator: its new categories leave them as they were.
SLICE_15_LINES = {
    "en": "0dda104cb1e7dc990357ce146b07be7972da94b19b5f6a8b82c9716363318e76",
    "es": "efbb86473e826589ac7ded9a360aa21ec48f5a52c57f9f80698afc6dafa78bf9",
}
SLICE_15_TERMS = "9eda47f08f9ba98c4616e13aa1770d7265f12c523a9caaed22d371f8516dbdd2"
_INITIAL = re.compile(r"[^\W\d_]\.")
_ID = re.compile(r"(en|es)-([a-z]+(?:-[a-z]+)?)-(\d{3})")
_NAME_WORD = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)?")


@pytest.fixture(scope="module")
def corpora() -> dict[str, list[Example]]:
    return {lang: load_corpus(CORPUS / f"persons.{lang}.jsonl") for lang in LANGUAGES}


def _category(example: Example) -> str:
    match = _ID.fullmatch(example.id)
    assert match is not None, example.id
    return match[2]


def _surface(example: Example, span: GoldSpan) -> str:
    return example.text[span.start : span.end]


def _persons(example: Example) -> dict[str, list[str]]:
    """The surfaces of each person, by entity id, in order of mention."""
    persons: dict[str, list[str]] = {}
    for span in example.spans:
        if span.entity_type == "PERSON":
            assert span.entity is not None, example.id
            persons.setdefault(span.entity, []).append(_surface(example, span))
    return persons


def _words(surfaces: list[str]) -> set[str]:
    return {word for surface in surfaces for word in normalize_key(surface).split()}


def _full_names(example: Example) -> list[str]:
    """The dictionary term of each person who has one, in order of position: the first of its
    mentions with the most words, if that has at least two. In `single`, the first mention
    itself when the initial is part of it, and the title before it otherwise (slice 17)."""
    found: list[tuple[int, str]] = []
    for entity in _persons(example):
        spans = [s for s in example.spans if s.entity_type == "PERSON" and s.entity == entity]
        longest = max(len(_surface(example, span).split()) for span in spans)
        if _category(example) == "single" and longest == 1:
            title = example.text[: spans[0].start].split()[-1]
            found.append((spans[0].start, f"{title} {_surface(example, spans[0])}"))
        elif longest >= 2:
            span = next(s for s in spans if len(_surface(example, s).split()) == longest)
            found.append((span.start, _surface(example, span)))
    return [surface for _, surface in sorted(found)]


def _outside_gold(example: Example, word: str, tail: str = "") -> bool:
    pattern = re.compile(rf"(?<!\w){re.escape(word)}(?!\w){tail}", re.IGNORECASE)
    return any(
        not any(span.start <= match.start() and match.end() <= span.end for span in example.spans)
        for match in pattern.finditer(example.text)
    )


# The generator --------------------------------------------------------------------------------


def test_check_reports_whether_the_files_differ_from_a_fresh_generation(tmp_path: Path) -> None:
    assert generator.main(["--out", str(tmp_path)]) == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "persons.en.jsonl",
        "persons.es.jsonl",
        "persons.terms.yaml",
    ]
    assert generator.main(["--check", "--out", str(tmp_path)]) == 0
    for name in ("persons.es.jsonl", "persons.terms.yaml"):
        path = tmp_path / name
        original = path.read_text(encoding="utf-8")
        path.write_text(original + "\n", encoding="utf-8")
        assert generator.main(["--check", "--out", str(tmp_path)]) == 1
        path.unlink()
        assert generator.main(["--check", "--out", str(tmp_path)]) == 1
        path.write_text(original, encoding="utf-8")
        assert generator.main(["--check", "--out", str(tmp_path)]) == 0


def test_the_generator_is_deterministic() -> None:
    assert generator.generate() == generator.generate()


def test_the_generator_has_two_templates_per_category_and_language() -> None:
    for lang in LANGUAGES:
        assert tuple(generator.TEMPLATES[lang]) == CATEGORIES
        assert all(len(templates) == 2 for templates in generator.TEMPLATES[lang].values())


class _FixedNames:
    """Stands in for the generator's name source: always the same persons."""

    def __init__(self, persons: dict[str, generator._Person]) -> None:
        self.lang = "en"
        self.rng = random.Random(0)  # noqa: S311 - test data
        self._persons = persons

    def persons(self, category: str, labels: set[str]) -> dict[str, generator._Person]:
        return dict(self._persons)


def _drawn(category: str, template: str, **persons: generator._Person) -> object:
    names = _FixedNames(persons)
    return generator._example(category, 0, template, names)  # type: ignore[arg-type]


def test_the_generator_rejects_a_name_word_used_outside_the_gold_spans() -> None:
    will = generator._Person("male", "Will", "Stone")
    assert _drawn("first", "{a} sent it. {a.first} called.", a=will) is not None
    assert _drawn("first", "{a} sent it. Will you call?", a=will) is None
    assert _drawn("first", "{a} sent it. the stone floor.", a=will) is None
    # The listed name of `sentence` may be used as a common word; its other words may not.
    assert _drawn("sentence", "{a} sent it. {common} {a.first} called.", a=will) is not None
    assert _drawn("sentence", "{a} sent it. {common} Stone tiles.", a=will) is None


def test_the_generator_rejects_persons_sharing_a_word_the_category_does_not_allow() -> None:
    alice = generator._Person("female", "Alice", "Smith")
    bob, other_alice = (
        generator._Person("male", "Bob", "Smith"),
        generator._Person("female", "Alice", "Brown"),
    )
    template = "{a} and {b} called. {a.first} paid."
    assert _drawn("first", template, a=alice, b=bob) is None
    assert _drawn("shared-last", template, a=alice, b=bob) is not None
    assert _drawn("shared-last", template, a=alice, b=other_alice) is None
    assert _drawn("shared-first", template, a=alice, b=other_alice) is not None
    assert _drawn("first", template, a=alice, b=generator._Person("male", "Bob", "Bob")) is None


def test_the_generator_rejects_a_shorter_form_that_contains_a_full_name_of_the_corpus() -> None:
    corpus = generator._Corpus()
    corpus.add(generator._Example("", (), ("Alice Johnson",), ("Alice",)))
    run = generator._Example("", (), ("Alice Mary Johnson",), ("ALICE  johnson", "Mary"))
    assert not corpus.accepts(run)
    assert corpus.accepts(generator._Example("", (), ("Alice Mary Johnson",), ("Mary Johnson",)))
    corpus.add(generator._Example("", (), ("Bob Ray Lee",), ("Ray Lee",)))
    assert not corpus.accepts(generator._Example("", (), ("Ray Lee",), ()))
    assert not corpus.accepts(generator._Example("", (), ("Ann Cole",), ("Ann Cole Moore",)))
    assert corpus.accepts(generator._Example("", (), ("Ann Cole",), ("Ann",)))
    assert list(corpus.terms.values()) == ["Alice Johnson", "Bob Ray Lee"]


# The corpus files -----------------------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGUAGES)
def test_each_language_has_20_examples_in_each_of_17_categories(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    examples = corpora[lang]
    assert len(examples) == 340
    assert {example.lang for example in examples} == {lang}
    assert [example.id for example in examples] == [
        f"{lang}-{category}-{number:03d}" for category in CATEGORIES for number in range(1, 21)
    ]


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_person_span_has_an_id_and_covers_a_bare_name(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    for example in corpora[lang]:
        persons = _persons(example)
        assert persons, example.id
        assert list(persons) == [f"p{number}" for number in range(1, len(persons) + 1)]
        for surface in (surface for surfaces in persons.values() for surface in surfaces):
            assert not unicodedata.category(surface[0]).startswith(("P", "Z")), example.id
            assert not unicodedata.category(surface[-1]).startswith(("P", "Z")), example.id
            if _category(example) != "lower":
                assert surface[0].isupper(), example.id
            words = surface.split()
            if _category(example) == "single" and _INITIAL.fullmatch(words[0]):
                words = words[1:]  # the initial belongs to the name (slice 17)
            for word in words:
                assert _NAME_WORD.fullmatch(word), example.id
                assert len(word) >= 3, example.id
                if (_category(example), lang, word) == ("don", "en", generator.DON):
                    continue  # the English first name of `don` (slice 17)
                assert word.casefold() not in generator.TITLES | generator.PARTICLES, example.id


@pytest.mark.parametrize("lang", LANGUAGES)
def test_lower_has_a_shorter_form_in_lower_case(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    for example in corpora[lang]:
        if _category(example) == "lower":
            assert any(surface.islower() for surface in _persons(example)["p1"]), example.id


@pytest.mark.parametrize("lang", LANGUAGES)
def test_persons_share_a_word_only_where_the_category_says_so(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    for example in corpora[lang]:
        category, persons = _category(example), _persons(example)
        if category in {"shared-last", "shared-first", "other"}:
            assert len(persons) == 2, example.id
        shared = _words(persons["p1"]) & _words(persons.get("p2", []))
        if category in {"shared-last", "shared-first"}:
            assert shared, example.id
        elif category == "other":
            # The second person is named only by the first word of the first one's name.
            (word,) = _words(persons["p2"])
            assert word == normalize_key(persons["p1"][0]).split()[0], example.id
            assert shared == {word}, example.id
        else:
            assert not shared, example.id


@pytest.mark.parametrize("lang", LANGUAGES)
def test_in_word_and_sentence_the_word_from_the_list_also_occurs_outside_every_gold_span(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    words = {*generator.WORD_SURNAMES[lang], *generator.WORD_FIRST_NAMES[lang]}
    for example in corpora[lang]:
        if _category(example) in {"word", "sentence"}:
            (word,) = words & set(" ".join(_persons(example)["p1"]).split())
            assert _outside_gold(example, word), example.id


@pytest.mark.parametrize("lang", LANGUAGES)
def test_only_org_has_an_organisation_and_it_holds_the_persons_surname(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    for example in corpora[lang]:
        orgs = [span for span in example.spans if span.entity_type != "PERSON"]
        if _category(example) != "org":
            assert not orgs, example.id
            continue
        (org,) = orgs
        assert (org.entity_type, org.entity) == ("ORG", "o1"), example.id
        surname = _surface(example, org).split()[0]
        assert surname in _persons(example)["p1"], example.id
        assert surname in _persons(example)["p1"][0].split(), example.id


@pytest.mark.parametrize("lang", LANGUAGES)
def test_no_name_word_occurs_outside_the_gold_spans_except_the_common_word(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    common = {*generator.WORD_SURNAMES[lang], *generator.WORD_FIRST_NAMES[lang]}
    for example in corpora[lang]:
        for surfaces in _persons(example).values():
            words = {word for surface in surfaces for word in surface.split()}
            words |= {part for word in words for part in word.split("-")}
            for word in words:
                if _category(example) in {"word", "sentence"} and word in common:
                    continue
                # `Don` also starts the common word "Don't" in `don` (slice 17).
                tail = r"(?!'t\b)" if _category(example) == "don" and word == "Don" else ""
                assert not _outside_gold(example, word, tail), example.id


def test_the_dictionary_holds_the_term_of_every_person_once_in_drawing_order(
    corpora: dict[str, list[Example]],
) -> None:
    # The categories of slice 17 are drawn after the others of both languages.
    terms = load_terms(CORPUS / "persons.terms.yaml")
    assert {term.entity_type for term in terms} == {"PERSON"}
    assert all(term.aliases == () for term in terms)
    expected: dict[str, str] = {}
    for new in (False, True):
        for lang in LANGUAGES:
            for example in corpora[lang]:
                if (_category(example) in generator.NEW_CATEGORIES) != new:
                    continue
                for surface in _full_names(example):
                    expected.setdefault(normalize_key(surface), surface)
    assert [term.term for term in terms] == list(expected.values())
    assert (CORPUS / "persons.terms.yaml").read_text(encoding="utf-8").startswith("# ")


def test_no_shorter_form_of_several_words_contains_a_full_name_of_the_corpus(
    corpora: dict[str, list[Example]],
) -> None:
    full = {normalize_key(term.term) for term in load_terms(CORPUS / "persons.terms.yaml")}
    for lang in LANGUAGES:
        for example in corpora[lang]:
            names = set(map(normalize_key, _full_names(example)))
            for surfaces in _persons(example).values():
                for surface in surfaces:
                    words = normalize_key(surface).split()
                    if normalize_key(surface) in names:
                        continue
                    for size in range(2, len(words) + 1):
                        for start in range(len(words) - size + 1):
                            part = " ".join(words[start : start + size])
                            assert part not in full, example.id


# The categories of slice 17 ------------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGUAGES)
def test_the_first_280_lines_are_those_of_slice_15(lang: str) -> None:
    lines = (CORPUS / f"persons.{lang}.jsonl").read_text(encoding="utf-8").splitlines(True)
    assert hashlib.sha256("".join(lines[:280]).encode()).hexdigest() == SLICE_15_LINES[lang]


def test_the_generator_still_writes_the_lines_and_entries_of_slice_15() -> None:
    # With the Faker version of uv.lock: the new categories are drawn after the old ones.
    files = generator.generate()
    for lang in LANGUAGES:
        lines = files[f"persons.{lang}.jsonl"].splitlines(True)
        assert hashlib.sha256("".join(lines[:280]).encode()).hexdigest() == SLICE_15_LINES[lang]
    lines = files["persons.terms.yaml"].splitlines(True)
    entries = lines[lines.index("terms:\n") + 1 :]
    assert hashlib.sha256("".join(entries[: 2 * 719]).encode()).hexdigest() == SLICE_15_TERMS


def test_the_first_dictionary_entries_are_those_of_slice_15() -> None:
    lines = (CORPUS / "persons.terms.yaml").read_text(encoding="utf-8").splitlines(True)
    entries = lines[lines.index("terms:\n") + 1 :]
    assert len(entries) == 2 * 839
    assert hashlib.sha256("".join(entries[: 2 * 719]).encode()).hexdigest() == SLICE_15_TERMS


def _of(corpora: dict[str, list[Example]], lang: str, category: str) -> list[Example]:
    found = [example for example in corpora[lang] if _category(example) == category]
    assert len(found) == 20
    return found


@pytest.mark.parametrize("lang", LANGUAGES)
def test_single_names_its_person_by_a_title_or_an_initial_and_the_surname_only(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    titles = {"en": ("Ms.", "Mrs.", "Mr.", "Dr."), "es": ("Sra.", "Sr.", "Dra.", "Dr.")}[lang]
    kinds = set()
    for example in _of(corpora, lang, "single"):
        (surfaces,) = _persons(example).values()
        first, *rest = surfaces
        words = first.split()
        assert all(len(surface.split()) == 1 for surface in rest), example.id
        assert rest == [words[-1]], example.id
        if len(words) == 2:
            assert _INITIAL.fullmatch(words[0]), example.id
            kinds.add("initial")
        else:
            assert example.text[: example.text.index(first)].split()[-1] in titles, example.id
            kinds.add("title")
    assert kinds == {"initial", "title"}


@pytest.mark.parametrize("lang", LANGUAGES)
def test_hyphen_has_a_hyphenated_name_word_and_later_one_part_alone(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    for example in _of(corpora, lang, "hyphen"):
        (surfaces,) = _persons(example).values()
        full, part = surfaces
        (hyphenated,) = [word for word in full.split() if "-" in word]
        head, tail = hyphenated.split("-")
        assert part in {head, tail}, example.id
        assert (part == head) == (full.split()[0] == hyphenated), example.id


def test_english_don_has_the_first_name_don_and_the_common_word_dont(
    corpora: dict[str, list[Example]],
) -> None:
    for example in _of(corpora, "en", "don"):
        (surfaces,) = _persons(example).values()
        assert surfaces[0].split() == ["Don", surfaces[0].split()[1]], example.id
        assert "Don't" in example.text, example.id
        assert all("Don't" not in surface for surface in surfaces), example.id


def test_spanish_don_has_the_title_before_a_full_name_outside_the_span(
    corpora: dict[str, list[Example]],
) -> None:
    for example in _of(corpora, "es", "don"):
        (spans,) = [[s for s in example.spans if s.entity == "p1"]]
        full = spans[0]
        assert len(_surface(example, full).split()) == 3, example.id
        before = example.text[: full.start].split()[-1]
        assert before in {"don", "doña", "Don", "Doña"}, example.id


def test_the_generator_lets_dont_stand_outside_the_spans_but_no_other_don() -> None:
    don = generator._Person("male", "Don", "Fowler")
    assert _drawn("don", "Don't wait: {a} called. {a.first} paid.", a=don) is not None
    assert _drawn("don", "{a} called. Don paid.", a=don) is None
    assert _drawn("first", "Don't wait: {a} called.", a=don) is None


def test_the_generator_rejects_a_hyphen_part_used_outside_the_gold_spans() -> None:
    anne = generator._Person("female", "Anne-Marie", "Fowler")
    assert _drawn("hyphen", "{a} called. {a.first_head} paid.", a=anne) is not None
    assert _drawn("hyphen", "{a} called. Marie paid.", a=anne) is None
    assert _drawn("hyphen", "{a} called.", a=generator._Person("male", "Ann-Ann", "Lee")) is None


def test_the_generator_keeps_new_terms_out_of_earlier_texts_and_earlier_terms_out_of_new_ones() -> (
    None
):
    corpus = generator._Corpus()
    corpus.add(
        generator._Example("Marie Fowler called. Dr. Holloway paid.", (), ("Marie Fowler",), ())
    )
    hyphen = generator._Example("Anne-Marie Fowler called.", (), ("Anne-Marie Fowler",), ())
    assert not corpus.isolated(hyphen)
    single = generator._Example("Dr. Holloway wrote.", (), ("Dr. Holloway",), ())
    assert not corpus.isolated(single)
    assert corpus.isolated(generator._Example("Mr. Holloway wrote.", (), ("Mr. Holloway",), ()))
    assert corpus.isolated(generator._Example("Nobody wrote.", (), (), ()))


# The harness on the corpus --------------------------------------------------------------------


@pytest.fixture(scope="module")
def reports(corpora: dict[str, list[Example]]) -> dict[str, Report]:
    """The corpus with its dictionary of full names, as CI evaluates it."""
    pseudonymizer = build_pseudonymizer(load_settings(CORPUS / "persons.yaml", {}))
    return {lang: evaluate(pseudonymizer, corpora[lang]) for lang in LANGUAGES}


@pytest.mark.parametrize("lang", LANGUAGES)
def test_with_the_dictionary_there_is_no_round_trip_failure(
    reports: dict[str, Report], lang: str
) -> None:
    assert reports[lang].round_trip_failures == 0


@pytest.mark.parametrize("lang", LANGUAGES)
def test_with_the_dictionary_every_person_span_of_full_is_fully_covered(
    reports: dict[str, Report], lang: str
) -> None:
    misses = [miss for miss in reports[lang].misses if miss.example_id.startswith(f"{lang}-full-")]
    assert misses == []
    assert any(score.entity_type == "PERSON" for score in reports[lang].linking)

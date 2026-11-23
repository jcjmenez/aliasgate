import generate_person_corpus as generator
from aliasgate.common_words import COMMON_WORDS
from aliasgate.mapping import normalize_key
from aliasgate.names import TITLES


def test_every_key_is_its_own_normal_form_without_whitespace() -> None:
    assert COMMON_WORDS
    for key in COMMON_WORDS:
        assert key == normalize_key(key)
        assert key.split() == [key]


def test_the_list_has_the_633_keys_of_the_slice_file() -> None:
    assert len(COMMON_WORDS) == 633


def test_it_holds_the_corpus_names_that_are_common_words_and_closed_class_words() -> None:
    for lang in ("en", "es"):
        for name in (*generator.WORD_SURNAMES[lang], *generator.WORD_FIRST_NAMES[lang]):
            assert normalize_key(name) in COMMON_WORDS
    assert {"and", "para", "con", "the", "don", "won"} <= COMMON_WORDS


def test_its_only_titles_are_the_spanish_courtesy_titles() -> None:
    assert {"don", "doña"} == COMMON_WORDS & TITLES


def test_it_keeps_accents_and_leaves_out_names_that_are_not_common_words() -> None:
    assert {"adiós", "ángel", "jesús", "según", "río"} <= COMMON_WORDS
    assert not {"larry", "priscila", "johnson", "pérez", "alice"} & COMMON_WORDS

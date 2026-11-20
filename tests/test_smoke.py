import aliasgate


def test_package_exposes_version() -> None:
    assert aliasgate.__version__ == "0.1.0"

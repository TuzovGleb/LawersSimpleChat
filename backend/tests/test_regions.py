from app.search.regions import (
    REGION_REFERENCE,
    region_code_from_catalog,
    region_code_from_name,
)


def test_region_code_from_name():
    assert region_code_from_name("Нижегородская область") == 52
    assert region_code_from_name("Красноярский край") == 24
    assert region_code_from_name("  Ленинградская область ") == 47
    assert region_code_from_name("Неведомая область") is None
    assert region_code_from_name(None) is None


def test_region_code_from_catalog_prefers_subj():
    # New-format catalogs carry an explicit numeric subj (string or int).
    assert region_code_from_catalog({"subj": "47", "region": "Ленинградская область"}) == 47
    assert region_code_from_catalog({"subj": 24}) == 24


def test_region_code_from_catalog_falls_back_to_region_name():
    # Old-format catalogs (e.g. Нижегородская) have no subj — resolve by name.
    assert region_code_from_catalog({"region": "Нижегородская область"}) == 52


def test_region_code_from_catalog_unknown_returns_none():
    assert region_code_from_catalog({"region": "Атлантида"}) is None
    assert region_code_from_catalog({}) is None
    assert region_code_from_catalog(None) is None


def test_region_reference_lists_codes_with_names():
    assert "52 Нижегородская область" in REGION_REFERENCE
    assert "24 Красноярский край" in REGION_REFERENCE
    assert "91 Крым" in REGION_REFERENCE

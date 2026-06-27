from app.search.case_types import (
    CASE_TYPE_REFERENCE,
    case_type_from_catalog,
    case_type_from_name,
)


def test_case_type_from_name():
    assert case_type_from_name("уголовные") == "criminal"
    assert case_type_from_name("Гражданские") == "civil"
    assert case_type_from_name("  административные ") == "administrative"
    assert case_type_from_name("неведомые") is None
    assert case_type_from_name(None) is None


def test_case_type_from_catalog_prefers_delo_table():
    # The machine-stable marker wins even if category is missing/inconsistent.
    catalog = {"category": "уголовные", "deloFilter": {"delo_table": "u1_case"}}
    assert case_type_from_catalog(catalog) == "criminal"
    assert case_type_from_catalog({"deloFilter": {"delo_table": "g1_case"}}) == "civil"


def test_case_type_from_catalog_falls_back_to_category():
    # Old-format catalogs carry no deloFilter — resolve by the category label.
    assert case_type_from_catalog({"category": "уголовные"}) == "criminal"


def test_case_type_from_catalog_unknown_returns_none():
    assert case_type_from_catalog({"deloFilter": {"delo_table": "x9_case"}}) is None
    assert case_type_from_catalog({"category": "морские"}) is None
    assert case_type_from_catalog({}) is None
    assert case_type_from_catalog(None) is None


def test_case_type_reference_lists_codes_with_labels():
    assert "civil (гражданские)" in CASE_TYPE_REFERENCE
    assert "criminal (уголовные)" in CASE_TYPE_REFERENCE

from unittest.mock import MagicMock

from app.search.index import (
    delete_superseded_indices,
    generate_decision_id,
    normalize_case,
    parse_russian_date,
)


def test_parse_russian_date():
    assert parse_russian_date("16.02.2026") == "2026-02-16"
    assert parse_russian_date("2026-04-13") == "2026-04-13"
    assert parse_russian_date("") is None


def test_generate_decision_id_is_stable():
    first = generate_decision_id("52RS0001", "2-2728/2026")
    second = generate_decision_id("52RS0001", "2-2728/2026")
    assert first == second
    assert len(first) == 32


def test_normalize_case_builds_search_document():
    case = {
        "uid": "52RS0001-02-2026-000770-38",
        "caseNumber": "2-2728/2026",
        "actText": "Решение суда",
        "actTitle": "Решение по гражданскому делу",
        "category": ["Трудовые споры", "Зарплата"],
        "participants": [{"role": "plaintiff", "name": "Истец"}],
        "judge": "Морокова Е.О.",
        "filingDate": "16.02.2026",
        "decisionDate": "13.04.2026",
        "decisionResult": "Удовлетворен",
        "resultType": "granted",
        "actUrl": "https://example.com/act",
        "caseDetailsUrl": "https://example.com/case",
    }
    page_meta = {"courtName": "Автозаводский районный суд", "vnkod": "52RS0001"}

    document = normalize_case(case, page_meta)

    assert document is not None
    assert document["_id"] == generate_decision_id("52RS0001", "2-2728/2026")
    assert document["court_uid"] == "52RS0001-02-2026-000770-38"
    assert document["court_name"] == "Автозаводский районный суд"
    assert document["category"] == "Трудовые споры > Зарплата"
    assert document["participants_names"] == "Истец"
    assert document["decision_date"] == "2026-04-13"


def test_normalize_case_works_without_court_uid():
    case = {
        "caseNumber": "2-999/2026",
        "actText": "Решение без УИД",
    }
    page_meta = {"vnkod": "52RS0001"}

    document = normalize_case(case, page_meta)

    assert document is not None
    assert document["court_uid"] is None
    assert document["case_number"] == "2-999/2026"


def test_normalize_case_skips_missing_act_text():
    assert normalize_case({"caseNumber": "2-1/2026"}, {}) is None


def test_normalize_case_derives_region_code_from_vnkod():
    case = {"caseNumber": "2-2728/2026", "actText": "Решение суда"}
    document = normalize_case(case, {"vnkod": "52RS0001"})
    assert document is not None
    assert document["region_code"] == 52


def test_normalize_case_region_code_none_for_non_numeric_or_missing_vnkod():
    case = {"caseNumber": "2-2728/2026", "actText": "Решение суда"}
    # Malformed / missing prefix yields None rather than mapping to a wrong region.
    assert normalize_case(case, {"vnkod": "XXRS0001"})["region_code"] is None
    assert normalize_case(case, {"vnkod": ""})["region_code"] is None


def test_normalize_case_prefers_catalog_region_over_vnkod():
    case = {"caseNumber": "2-1/2026", "actText": "Решение суда"}
    # Таймырский (vnkod prefix 84) court belongs to Красноярский край (24) per catalog.
    doc = normalize_case(case, {"vnkod": "84RS0001", "region_code": 24})
    assert doc["region_code"] == 24


def test_normalize_case_uses_catalog_region_when_vnkod_empty():
    case = {"caseNumber": "2-2/2026", "actText": "Решение суда"}
    # Областной суд has no vnkod, but the dataset region still applies.
    doc = normalize_case(case, {"vnkod": "", "region_code": 52})
    assert doc["region_code"] == 52


def test_normalize_case_carries_case_type_from_page_meta():
    case = {"caseNumber": "1-66/2024", "actText": "Приговор"}
    doc = normalize_case(case, {"vnkod": "52RS0001", "case_type": "criminal"})
    assert doc["case_type"] == "criminal"


def test_normalize_case_case_type_none_when_absent():
    case = {"caseNumber": "2-1/2026", "actText": "Решение суда"}
    assert normalize_case(case, {"vnkod": "52RS0001"})["case_type"] is None


def test_delete_superseded_indices_keeps_current_and_aliased():
    client = MagicMock()
    client.indices.exists_alias.return_value = True
    client.indices.get_alias.return_value = {"court_decisions_v3": {}}
    client.indices.get.return_value = {
        "court_decisions_v1": {},
        "court_decisions_v2": {},
        "court_decisions_v3": {},
    }

    removed = delete_superseded_indices(
        client, index_name="court_decisions_v3", alias="court_decisions"
    )

    # v3 is the current/aliased index and must survive; older versions go.
    assert sorted(removed) == ["court_decisions_v1", "court_decisions_v2"]
    deleted = {c.kwargs["index"] for c in client.indices.delete.call_args_list}
    assert deleted == {"court_decisions_v1", "court_decisions_v2"}
    assert "court_decisions_v3" not in deleted

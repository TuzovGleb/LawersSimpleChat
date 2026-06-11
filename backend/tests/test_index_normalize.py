from app.search.index import normalize_case, parse_russian_date


def test_parse_russian_date():
    assert parse_russian_date("16.02.2026") == "2026-02-16"
    assert parse_russian_date("2026-04-13") == "2026-04-13"
    assert parse_russian_date("") is None


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
    assert document["_id"] == "52RS0001-02-2026-000770-38"
    assert document["court_name"] == "Автозаводский районный суд"
    assert document["category"] == "Трудовые споры > Зарплата"
    assert document["participants_names"] == "Истец"
    assert document["decision_date"] == "2026-04-13"


def test_normalize_case_skips_missing_act_text():
    assert normalize_case({"uid": "x"}, {}) is None

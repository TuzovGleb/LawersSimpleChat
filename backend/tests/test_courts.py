from app.search.courts import (
    COURT_CODE_TO_NAME,
    COURT_REFERENCE,
    COURTS_WITH_DATA,
    court_names_from_codes,
)


def test_court_names_from_codes_resolves_and_dedupes():
    names = court_names_from_codes(["ksoyu-1", "ksoyu-1", "vs-rf"])
    assert names == [
        "Первый кассационный суд общей юрисдикции",
        "Верховный Суд Российской Федерации",
    ]


def test_court_names_from_codes_drops_unknown_and_empty():
    assert court_names_from_codes(["ksoyu-99", "  ", "unknown"]) == []
    assert court_names_from_codes(None) == []
    assert court_names_from_codes([]) == []


def test_ksoyu_and_vs_have_verified_names_and_data():
    # These are verified byte-for-byte against the index, so their filter works.
    assert COURT_CODE_TO_NAME["ksoyu-4"] == "Четвёртый кассационный суд общей юрисдикции"
    assert COURT_CODE_TO_NAME["vs-rf"] == "Верховный Суд Российской Федерации"
    for code in ("vs-rf", "ksoyu-1", "ksoyu-9"):
        assert code in COURTS_WITH_DATA


def test_arbitration_courts_present_but_marked_no_data():
    # Forward-compat scaffold: codes exist, but no indexed practice yet.
    for code in ("1aac", "21aac", "as-vvo", "as-mo", "sip"):
        assert code in COURT_CODE_TO_NAME
        assert code not in COURTS_WITH_DATA


def test_reference_marks_only_dataless_courts():
    assert "ksoyu-1 (Первый кассационный суд общей юрисдикции)" in COURT_REFERENCE
    assert "vs-rf (Верховный Суд Российской Федерации)" in COURT_REFERENCE
    assert "1aac (Первый арбитражный апелляционный суд — данных пока нет)" in COURT_REFERENCE
    assert "sip (Суд по интеллектуальным правам — данных пока нет)" in COURT_REFERENCE
    # a with-data court must NOT carry the marker
    assert "ksoyu-1 (Первый кассационный суд общей юрисдикции — данных пока нет)" not in COURT_REFERENCE

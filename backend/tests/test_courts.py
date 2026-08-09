from app.search.courts import (
    COURT_CODE_TO_NAME,
    COURT_REFERENCE,
    COURTS_WITH_DATA,
    court_code_from_name,
    known_court_codes,
)


def test_known_court_codes_keeps_valid_dedupes_drops_unknown():
    assert known_court_codes(["ksoyu-1", "ksoyu-1", "vs-rf"]) == ["ksoyu-1", "vs-rf"]
    assert known_court_codes(["ksoyu-99", "  ", "unknown"]) == []
    assert known_court_codes(None) == []
    assert known_court_codes([]) == []


def test_court_code_from_name_reverse_maps_higher_courts_only():
    assert court_code_from_name("Первый кассационный суд общей юрисдикции") == "ksoyu-1"
    assert court_code_from_name("Верховный Суд Российской Федерации") == "vs-rf"
    assert court_code_from_name("  Четвёртый кассационный суд общей юрисдикции ") == "ksoyu-4"
    # Ordinary courts and unknown names carry no code (reached via regions).
    assert court_code_from_name("Автозаводский районный суд г. Нижний Новгород") is None
    assert court_code_from_name("АС Нижегородской области") is None
    assert court_code_from_name(None) is None


def test_ksoyu_and_vs_have_verified_names_and_data():
    assert COURT_CODE_TO_NAME["ksoyu-4"] == "Четвёртый кассационный суд общей юрисдикции"
    assert COURT_CODE_TO_NAME["vs-rf"] == "Верховный Суд Российской Федерации"
    for code in ("vs-rf", "ksoyu-1", "ksoyu-9"):
        assert code in COURTS_WITH_DATA


def test_every_mapped_court_now_has_data():
    # All 21 appellate courts, 10 okrug courts, СИП, ВС and the 9 КСОЮ are
    # indexed — nothing in the mapping is scaffold any more.
    assert set(COURT_CODE_TO_NAME) == set(COURTS_WITH_DATA)


def test_arbitration_appellate_name_matches_scraper_form():
    # The scraper stores "1 арбитражный апелляционный суд" (digit + lowercase);
    # the court filter is an exact court_name match, so the mapping must agree.
    assert COURT_CODE_TO_NAME["1aac"] == "1 арбитражный апелляционный суд"
    assert court_code_from_name("1 арбитражный апелляционный суд") == "1aac"
    assert "1aac" in COURTS_WITH_DATA


def test_reference_marks_only_dataless_courts():
    assert "ksoyu-1 (Первый кассационный суд общей юрисдикции)" in COURT_REFERENCE
    assert "vs-rf (Верховный Суд Российской Федерации)" in COURT_REFERENCE
    assert "1aac (1 арбитражный апелляционный суд)" in COURT_REFERENCE
    assert "as-vvo (АС Волго-Вятского округа)" in COURT_REFERENCE
    assert "2aac (2 арбитражный апелляционный суд)" in COURT_REFERENCE
    assert "9aac (9 арбитражный апелляционный суд)" in COURT_REFERENCE
    assert "10aac (10 арбитражный апелляционный суд)" in COURT_REFERENCE
    assert "as-mo (АС Московского округа)" in COURT_REFERENCE
    assert "sip (Суд по интеллектуальным правам)" in COURT_REFERENCE
    assert "2aac (2 арбитражный апелляционный суд)" in COURT_REFERENCE
    assert "9aac (9 арбитражный апелляционный суд)" in COURT_REFERENCE
    assert "10aac (10 арбитражный апелляционный суд)" in COURT_REFERENCE
    assert "ksoyu-1 (Первый кассационный суд общей юрисдикции — данных пока нет)" not in COURT_REFERENCE

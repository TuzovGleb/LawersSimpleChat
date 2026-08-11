from app.search.courts import (
    COURT_CODE_TO_NAME,
    COURT_NAME_ALIASES,
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


def test_names_match_each_scraper_naming_convention():
    # court_code is derived from court_name by exact match, so each of the three
    # naming conventions the scraper uses must be reproduced verbatim: КСОЮ/ВС
    # spelled out, appellate as digit + lowercase, okrug abbreviated to "АС".
    assert COURT_CODE_TO_NAME["vs-rf"] == "Верховный Суд Российской Федерации"
    assert COURT_CODE_TO_NAME["ksoyu-4"] == "Четвёртый кассационный суд общей юрисдикции"
    assert COURT_CODE_TO_NAME["1aac"] == "1 арбитражный апелляционный суд"
    assert COURT_CODE_TO_NAME["as-vvo"] == "АС Волго-Вятского округа"
    for name, code in (
        ("Верховный Суд Российской Федерации", "vs-rf"),
        ("1 арбитражный апелляционный суд", "1aac"),
        ("АС Волго-Вятского округа", "as-vvo"),
    ):
        assert court_code_from_name(name) == code


def test_every_mapped_court_has_data():
    # All 21 appellate courts, 10 okrug courts, СИП, ВС and the 9 КСОЮ are
    # indexed — nothing in the mapping is scaffold any more.
    assert set(COURT_CODE_TO_NAME) == set(COURTS_WITH_DATA)


def test_reference_lists_every_court_unmarked():
    # With data everywhere, no entry may carry the «данных пока нет» marker.
    assert "данных пока нет" not in COURT_REFERENCE
    for code, name in COURT_CODE_TO_NAME.items():
        assert f"{code} ({name})" in COURT_REFERENCE


def test_scraper_name_aliases_resolve_to_the_same_court():
    # The two corpora spell ВС РФ differently; both must reach vs-rf, or half its
    # practice would be invisible to the courts filter.
    assert court_code_from_name("Верховный Суд Российской Федерации") == "vs-rf"
    assert court_code_from_name("Верховный Суд РФ") == "vs-rf"
    # Aliases are extra spellings only — never a court missing from the mapping.
    assert set(COURT_NAME_ALIASES.values()) <= set(COURT_CODE_TO_NAME)

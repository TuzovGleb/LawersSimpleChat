"""Curated notable higher courts — the only courts the court filter exposes.

Region filtering already covers the ~2000 first-instance/appeal courts by
subject. But the higher courts that span many subjects — Верховный Суд РФ, the 9
cassation courts of general jurisdiction (КСОЮ), and the arbitration
appellate/okrug courts — are not usefully addressed by a region code (a КСОЮ
covers a whole okrug; a case's region is where it originated, not which higher
court decided it). This module maps a short code to the EXACT ``court_name``
stored at index time, so ``search_court_practice`` can offer a ``courts`` filter
for them, by analogy with ``regions``.

The filter is an exact ``terms`` match on ``court_name``, so a code's name must
match the indexed string byte-for-byte. Courts we have NOT indexed yet
(arbitration appellate/okrug/СИП) are listed for forward-compatibility but their
names are PROVISIONAL — the scraper abbreviates ("АС Нижегородской области"), so
verify against real data before trusting them. ``COURTS_WITH_DATA`` marks which
codes actually have indexed practice, exactly like ``CASE_TYPES_WITH_DATA``.
"""

# code -> exact court_name as stored in the index.
COURT_CODE_TO_NAME: dict[str, str] = {
    # --- Верховный Суд РФ (высшая инстанция) — verified against the index ---
    "vs-rf": "Верховный Суд Российской Федерации",
    # --- Кассационные суды общей юрисдикции (КСОЮ) — verified against the index ---
    "ksoyu-1": "Первый кассационный суд общей юрисдикции",
    "ksoyu-2": "Второй кассационный суд общей юрисдикции",
    "ksoyu-3": "Третий кассационный суд общей юрисдикции",
    "ksoyu-4": "Четвёртый кассационный суд общей юрисдикции",
    "ksoyu-5": "Пятый кассационный суд общей юрисдикции",
    "ksoyu-6": "Шестой кассационный суд общей юрисдикции",
    "ksoyu-7": "Седьмой кассационный суд общей юрисдикции",
    "ksoyu-8": "Восьмой кассационный суд общей юрисдикции",
    "ksoyu-9": "Девятый кассационный суд общей юрисдикции",
    # --- Арбитражные апелляционные суды (21) — data pending, names PROVISIONAL ---
    "1aac": "Первый арбитражный апелляционный суд",
    "2aac": "Второй арбитражный апелляционный суд",
    "3aac": "Третий арбитражный апелляционный суд",
    "4aac": "Четвёртый арбитражный апелляционный суд",
    "5aac": "Пятый арбитражный апелляционный суд",
    "6aac": "Шестой арбитражный апелляционный суд",
    "7aac": "Седьмой арбитражный апелляционный суд",
    "8aac": "Восьмой арбитражный апелляционный суд",
    "9aac": "Девятый арбитражный апелляционный суд",
    "10aac": "Десятый арбитражный апелляционный суд",
    "11aac": "Одиннадцатый арбитражный апелляционный суд",
    "12aac": "Двенадцатый арбитражный апелляционный суд",
    "13aac": "Тринадцатый арбитражный апелляционный суд",
    "14aac": "Четырнадцатый арбитражный апелляционный суд",
    "15aac": "Пятнадцатый арбитражный апелляционный суд",
    "16aac": "Шестнадцатый арбитражный апелляционный суд",
    "17aac": "Семнадцатый арбитражный апелляционный суд",
    "18aac": "Восемнадцатый арбитражный апелляционный суд",
    "19aac": "Девятнадцатый арбитражный апелляционный суд",
    "20aac": "Двадцатый арбитражный апелляционный суд",
    "21aac": "Двадцать первый арбитражный апелляционный суд",
    # --- Арбитражные суды округов (кассация, 10) — data pending, names PROVISIONAL ---
    "as-vvo": "Арбитражный суд Волго-Вятского округа",
    "as-vso": "Арбитражный суд Восточно-Сибирского округа",
    "as-dvo": "Арбитражный суд Дальневосточного округа",
    "as-zso": "Арбитражный суд Западно-Сибирского округа",
    "as-mo": "Арбитражный суд Московского округа",
    "as-po": "Арбитражный суд Поволжского округа",
    "as-szo": "Арбитражный суд Северо-Западного округа",
    "as-sko": "Арбитражный суд Северо-Кавказского округа",
    "as-uo": "Арбитражный суд Уральского округа",
    "as-co": "Арбитражный суд Центрального округа",
    # --- Суд по интеллектуальным правам — data pending, name PROVISIONAL ---
    "sip": "Суд по интеллектуальным правам",
}

# Codes that actually have indexed practice (filter returns something). Keep in
# sync with the system prompt (prompt.py, секция [10]) when a new court's data
# lands: move its code here and re-verify its court_name against the index.
COURTS_WITH_DATA: frozenset[str] = frozenset(
    {"vs-rf", "ksoyu-1", "ksoyu-2", "ksoyu-3", "ksoyu-4", "ksoyu-5",
     "ksoyu-6", "ksoyu-7", "ksoyu-8", "ksoyu-9"}
)

# Human-readable "code — Название" reference for the tool's `courts` parameter.
# Codes without indexed data are marked so the model never filters on an empty
# court (mirrors CASE_TYPE_REFERENCE).
COURT_REFERENCE: str = ", ".join(
    f"{code} ({name})" if code in COURTS_WITH_DATA else f"{code} ({name} — данных пока нет)"
    for code, name in COURT_CODE_TO_NAME.items()
)


def court_names_from_codes(codes: list[str] | None) -> list[str]:
    """Resolve a list of court codes to their exact ``court_name`` strings.

    Unknown codes are dropped (the model was told to use only codes from the
    reference). Returns [] when nothing resolves, so the caller adds no filter
    and the search spans all courts.
    """
    if not codes:
        return []
    names: list[str] = []
    for code in codes:
        name = COURT_CODE_TO_NAME.get((code or "").strip())
        if name and name not in names:
            names.append(name)
    return names

"""Curated notable higher courts — the only courts the court filter exposes.

Region filtering already covers the ~2000 first-instance/appeal courts by
subject. But the higher courts that span many subjects — Верховный Суд РФ, the 9
cassation courts of general jurisdiction (КСОЮ), and the arbitration
appellate/okrug courts — are not usefully addressed by a region code (a КСОЮ
covers a whole okrug; a case's region is where it originated, not which higher
court decided it). This module maps a short code to the EXACT ``court_name``
stored at index time, so ``search_court_practice`` can offer a ``courts`` filter
for them, by analogy with ``regions``.

The court_code is derived from ``court_name`` at index time, so a code's name
must match what the scraper stores byte-for-byte — always verify a new court's
name against its dataset before trusting it (the scraper abbreviates: "АС
Волго-Вятского округа", "1 арбитражный апелляционный суд"). ``COURTS_WITH_DATA``
marks which codes actually have indexed practice, like ``CASE_TYPES_WITH_DATA``;
codes not in it are scaffold and are shown to the model as «данных пока нет».
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
    # --- Арбитражные апелляционные суды (21) ---
    # Name form verified against the indexed 1st AAC dataset: the scraper stores
    # "1 арбитражный апелляционный суд" (digit + lowercase), NOT the spelled-out
    # "Первый ...". The rest follow the same pattern; each is confirmed as its
    # data lands (only the ones in COURTS_WITH_DATA are searchable).
    "1aac": "1 арбитражный апелляционный суд",
    "2aac": "2 арбитражный апелляционный суд",
    "3aac": "3 арбитражный апелляционный суд",
    "4aac": "4 арбитражный апелляционный суд",
    "5aac": "5 арбитражный апелляционный суд",
    "6aac": "6 арбитражный апелляционный суд",
    "7aac": "7 арбитражный апелляционный суд",
    "8aac": "8 арбитражный апелляционный суд",
    "9aac": "9 арбитражный апелляционный суд",
    "10aac": "10 арбитражный апелляционный суд",
    "11aac": "11 арбитражный апелляционный суд",
    "12aac": "12 арбитражный апелляционный суд",
    "13aac": "13 арбитражный апелляционный суд",
    "14aac": "14 арбитражный апелляционный суд",
    "15aac": "15 арбитражный апелляционный суд",
    "16aac": "16 арбитражный апелляционный суд",
    "17aac": "17 арбитражный апелляционный суд",
    "18aac": "18 арбитражный апелляционный суд",
    "19aac": "19 арбитражный апелляционный суд",
    "20aac": "20 арбитражный апелляционный суд",
    "21aac": "21 арбитражный апелляционный суд",
    # --- Арбитражные суды округов (кассация, 10) ---
    # Name form verified against the datasets: the scraper abbreviates to
    # "АС <name> округа" (same as "АС Нижегородской области"), not the full
    # "Арбитражный суд ...". as-mo is the only one without data yet.
    "as-vvo": "АС Волго-Вятского округа",
    "as-vso": "АС Восточно-Сибирского округа",
    "as-dvo": "АС Дальневосточного округа",
    "as-zso": "АС Западно-Сибирского округа",
    "as-mo": "АС Московского округа",
    "as-po": "АС Поволжского округа",
    "as-szo": "АС Северо-Западного округа",
    "as-sko": "АС Северо-Кавказского округа",
    "as-uo": "АС Уральского округа",
    "as-co": "АС Центрального округа",
    # --- Суд по интеллектуальным правам (first instance + cassation) ---
    "sip": "Суд по интеллектуальным правам",
}

# Codes that actually have indexed practice (filter returns something). Keep in
# sync with the system prompt (prompt.py, секция [10]) when a new court's data
# lands: move its code here and re-verify its court_name against the index.
COURTS_WITH_DATA: frozenset[str] = frozenset(
    {"vs-rf", "ksoyu-1", "ksoyu-2", "ksoyu-3", "ksoyu-4", "ksoyu-5",
     "ksoyu-6", "ksoyu-7", "ksoyu-8", "ksoyu-9", "1aac", "2aac", "3aac", "4aac", "5aac", "6aac", "7aac", "8aac", "9aac",
     "10aac", "11aac", "12aac", "13aac", "14aac", "15aac", "16aac", "17aac",
     "18aac", "19aac", "20aac", "21aac",
     "as-vvo", "as-vso", "as-dvo", "as-zso", "as-mo", "as-po", "as-szo",
     "as-sko", "as-uo", "as-co", "sip"}
)

# Human-readable "code — Название" reference for the tool's `courts` parameter.
# Codes without indexed data are marked so the model never filters on an empty
# court (mirrors CASE_TYPE_REFERENCE).
COURT_REFERENCE: str = ", ".join(
    f"{code} ({name})" if code in COURTS_WITH_DATA else f"{code} ({name} — данных пока нет)"
    for code, name in COURT_CODE_TO_NAME.items()
)


# Reverse map, for deriving a doc's court_code from its court_name at index time.
COURT_NAME_TO_CODE: dict[str, str] = {name: code for code, name in COURT_CODE_TO_NAME.items()}


def court_code_from_name(court_name: str | None) -> str | None:
    """Map an exact ``court_name`` to its short code (slug), or None.

    Only the notable higher courts in the mapping get a code; ordinary
    district/regional courts return None (they are reached via ``regions``, not
    the court filter). Stored as ``court_code`` at index time so the ``courts``
    filter matches a stable slug, not the fragile display name.
    """
    if not isinstance(court_name, str):
        return None
    return COURT_NAME_TO_CODE.get(court_name.strip())


def known_court_codes(codes: list[str] | None) -> list[str]:
    """Keep only codes present in the mapping, de-duped, preserving order.

    The model was told to pass only codes from the reference; unknown codes are
    dropped so a typo silently widens rather than breaks the search. Returns []
    when nothing is valid, so the caller adds no court filter.
    """
    if not codes:
        return []
    seen: list[str] = []
    for code in codes:
        c = (code or "").strip()
        if c in COURT_CODE_TO_NAME and c not in seen:
            seen.append(c)
    return seen

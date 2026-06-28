"""Canonical RF region (субъект) codes — single source of truth.

Codes follow the СУДРФ court-code convention, which is what gets stored in the
``region_code`` field at index time and what the model passes to the
``regions`` search filter. This mostly matches the official коды субъектов РФ,
with the notable exception that Крым is 91 (not the official 82), because that
is the prefix СУДРФ actually uses in court codes.

Both the indexing pipeline (to resolve a dataset's region from its catalog) and
the ``search_court_practice`` tool (to document the ``regions`` parameter) read
from here, so the list never drifts between the two.
"""

REGION_CODE_TO_NAME: dict[int, str] = {
    1: "Адыгея",
    2: "Башкортостан",
    3: "Бурятия",
    4: "Республика Алтай",
    5: "Дагестан",
    6: "Ингушетия",
    7: "Кабардино-Балкария",
    8: "Калмыкия",
    9: "Карачаево-Черкесия",
    10: "Карелия",
    11: "Коми",
    12: "Марий Эл",
    13: "Мордовия",
    14: "Якутия (Саха)",
    15: "Северная Осетия — Алания",
    16: "Татарстан",
    17: "Тыва",
    18: "Удмуртия",
    19: "Хакасия",
    20: "Чечня",
    21: "Чувашия",
    22: "Алтайский край",
    23: "Краснодарский край",
    24: "Красноярский край",
    25: "Приморский край",
    26: "Ставропольский край",
    27: "Хабаровский край",
    28: "Амурская область",
    29: "Архангельская область",
    30: "Астраханская область",
    31: "Белгородская область",
    32: "Брянская область",
    33: "Владимирская область",
    34: "Волгоградская область",
    35: "Вологодская область",
    36: "Воронежская область",
    37: "Ивановская область",
    38: "Иркутская область",
    39: "Калининградская область",
    40: "Калужская область",
    41: "Камчатский край",
    42: "Кемеровская область",
    43: "Кировская область",
    44: "Костромская область",
    45: "Курганская область",
    46: "Курская область",
    47: "Ленинградская область",
    48: "Липецкая область",
    49: "Магаданская область",
    50: "Московская область",
    51: "Мурманская область",
    52: "Нижегородская область",
    53: "Новгородская область",
    54: "Новосибирская область",
    55: "Омская область",
    56: "Оренбургская область",
    57: "Орловская область",
    58: "Пензенская область",
    59: "Пермский край",
    60: "Псковская область",
    61: "Ростовская область",
    62: "Рязанская область",
    63: "Самарская область",
    64: "Саратовская область",
    65: "Сахалинская область",
    66: "Свердловская область",
    67: "Смоленская область",
    68: "Тамбовская область",
    69: "Тверская область",
    70: "Томская область",
    71: "Тульская область",
    72: "Тюменская область",
    73: "Ульяновская область",
    74: "Челябинская область",
    75: "Забайкальский край",
    76: "Ярославская область",
    77: "Москва",
    78: "Санкт-Петербург",
    79: "Еврейская АО",
    83: "Ненецкий АО",
    86: "Ханты-Мансийский АО — Югра",
    87: "Чукотский АО",
    89: "Ямало-Ненецкий АО",
    91: "Крым",
    92: "Севастополь",
    # Федеральные суды вне субъектов РФ. 99 не используется СУДРФ как код
    # субъекта, остаётся 2-значным (как требует док тула search_court_practice)
    # и читается как «федеральный / высший суд».
    99: "Верховный Суд РФ",
}

REGION_NAME_TO_CODE: dict[str, int] = {name: code for code, name in REGION_CODE_TO_NAME.items()}

# Human-readable "NN Название" list, used in the search tool's parameter docs.
REGION_REFERENCE: str = ", ".join(
    f"{code:02d} {name}" for code, name in sorted(REGION_CODE_TO_NAME.items())
)


def region_code_from_name(name: str | None) -> int | None:
    """Map a region name (as written in a dataset catalog) to its code."""
    if not isinstance(name, str):
        return None
    return REGION_NAME_TO_CODE.get(name.strip())


def region_code_from_catalog(catalog: dict | None) -> int | None:
    """Resolve a dataset's region code from its ``_catalog.json``.

    Prefers the explicit numeric ``subj`` code; falls back to matching the
    ``region`` name. Returns None when neither is usable (the caller then falls
    back to deriving the code from each court's vnkod prefix).
    """
    if not isinstance(catalog, dict):
        return None
    subj = catalog.get("subj")
    # subj is a positive субъект code; 0 (or non-positive) is a sentinel for
    # multi-region datasets — e.g. the cassation courts (КСОЮ) span many
    # subjects, so their catalog carries subj=0 and we fall through to per-case
    # resolution from each act's vnkod prefix at index time.
    if isinstance(subj, int) and not isinstance(subj, bool) and subj > 0:
        return subj
    if isinstance(subj, str) and subj.strip().isdigit() and int(subj.strip()) > 0:
        return int(subj.strip())
    return region_code_from_name(catalog.get("region"))

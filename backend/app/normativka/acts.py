"""Canonical table of the acting codices of the RF in the ИПС «Законодательство России».

``nd`` is the ИПС document id — the stable key every fetch is made by. Ten of
them were verified live against the wrapper ``<title>`` (ГК1, ГК2, ГПК, АПК,
ТК, КоАП, УПК, НК1, НК2, СК); the rest come from the official catalog
enumeration (вид = «Кодекс», статус = действует, textpres=yes). The scraper
re-verifies every nd against the fetched document title and refuses to ingest
on mismatch, so a stale entry here fails loudly instead of indexing the wrong
act.

Aliases are the lookup keys lawyers actually use («ТК РФ», «ГПК»). They are
matched case-insensitively after normalization (см. resolve_act). «ТК»
намеренно указывает на Трудовой кодекс, не на Таможенный кодекс 1993 года —
в живых запросах юристов «ТК» это трудовое право.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class KnownAct:
    nd: str                 # ИПС document id
    name: str               # canonical full name
    number: str             # official number, e.g. «197-ФЗ»
    adoption_date: str      # YYYY-MM-DD
    aliases: tuple[str, ...] = field(default_factory=tuple)
    kind: str = "kodeks"    # kodeks | fz | zakon_rf


CODICES: tuple[KnownAct, ...] = (
    KnownAct("102380990", "Кодекс административного судопроизводства Российской Федерации",
             "21-ФЗ", "2015-03-08", ("КАС РФ", "КАС")),
    KnownAct("102110716", "Гражданский кодекс Российской Федерации. Часть четвертая",
             "230-ФЗ", "2006-12-18", ("ГК РФ часть 4", "ГК ч.4", "часть четвертая ГК")),
    KnownAct("102110364", "Лесной кодекс Российской Федерации",
             "200-ФЗ", "2006-12-04", ("ЛК РФ",)),
    KnownAct("102107048", "Водный кодекс Российской Федерации",
             "74-ФЗ", "2006-06-03", ("ВК РФ", "Водный кодекс")),
    KnownAct("102090643", "Градостроительный кодекс Российской Федерации",
             "190-ФЗ", "2004-12-29", ("ГрК РФ", "ГрК")),
    KnownAct("102090645", "Жилищный кодекс Российской Федерации",
             "188-ФЗ", "2004-12-29", ("ЖК РФ", "ЖК")),
    KnownAct("102078828", "Гражданский процессуальный кодекс Российской Федерации",
             "138-ФЗ", "2002-11-14", ("ГПК РФ", "ГПК")),
    KnownAct("102079219", "Арбитражный процессуальный кодекс Российской Федерации",
             "95-ФЗ", "2002-07-24", ("АПК РФ", "АПК")),
    KnownAct("102074279", "Трудовой кодекс Российской Федерации",
             "197-ФЗ", "2001-12-30", ("ТК РФ", "ТК")),
    KnownAct("102074277", "Кодекс Российской Федерации об административных правонарушениях",
             "195-ФЗ", "2001-12-30", ("КоАП РФ", "КоАП")),
    KnownAct("102073942", "Уголовно-процессуальный кодекс Российской Федерации",
             "174-ФЗ", "2001-12-18", ("УПК РФ", "УПК")),
    KnownAct("102073578", "Гражданский кодекс Российской Федерации. Часть третья",
             "146-ФЗ", "2001-11-26", ("ГК РФ часть 3", "ГК ч.3", "часть третья ГК")),
    KnownAct("102073184", "Земельный кодекс Российской Федерации",
             "136-ФЗ", "2001-10-25", ("ЗК РФ", "ЗК")),
    KnownAct("102069974", "Кодекс внутреннего водного транспорта Российской Федерации",
             "24-ФЗ", "2001-03-07", ("КВВТ РФ", "КВВТ")),
    KnownAct("102067058", "Налоговый кодекс Российской Федерации. Часть вторая",
             "117-ФЗ", "2000-08-05", ("НК РФ часть 2", "НК ч.2", "часть вторая НК")),
    KnownAct("102059464", "Кодекс торгового мореплавания Российской Федерации",
             "81-ФЗ", "1999-04-30", ("КТМ РФ", "КТМ")),
    KnownAct("102054722", "Налоговый кодекс Российской Федерации. Часть первая",
             "146-ФЗ", "1998-07-31", ("НК РФ часть 1", "НК ч.1", "часть первая НК", "НК РФ", "НК")),
    KnownAct("102054721", "Бюджетный кодекс Российской Федерации",
             "145-ФЗ", "1998-07-31", ("БК РФ", "БК")),
    KnownAct("102046246", "Воздушный кодекс Российской Федерации",
             "60-ФЗ", "1997-03-19", ("ВзК РФ", "Воздушный кодекс")),
    KnownAct("102045146", "Уголовно-исполнительный кодекс Российской Федерации",
             "1-ФЗ", "1997-01-08", ("УИК РФ", "УИК")),
    KnownAct("102041891", "Уголовный кодекс Российской Федерации",
             "63-ФЗ", "1996-06-13", ("УК РФ", "УК")),
    KnownAct("102039276", "Гражданский кодекс Российской Федерации. Часть вторая",
             "14-ФЗ", "1996-01-26", ("ГК РФ часть 2", "ГК ч.2", "часть вторая ГК")),
    KnownAct("102038925", "Семейный кодекс Российской Федерации",
             "223-ФЗ", "1995-12-29", ("СК РФ", "СК")),
    KnownAct("102033239", "Гражданский кодекс Российской Федерации. Часть первая",
             "51-ФЗ", "1994-11-30", ("ГК РФ часть 1", "ГК ч.1", "часть первая ГК", "ГК РФ", "ГК")),
    # Таможенный кодекс 1993 года числится в ИПС действующим, но фактически
    # вытеснен ТК ЕАЭС; держим в каталоге (данные официальные), алиас «ТК»
    # ему сознательно не даём.
    KnownAct("102024315", "Таможенный кодекс Российской Федерации",
             "5221-I", "1993-06-18", ("Таможенный кодекс 1993",)),
)


def _norm(value: str) -> str:
    return " ".join(value.lower().replace("ё", "е").split())


_ALIAS_INDEX: dict[str, KnownAct] = {}
for _act in CODICES:
    for _key in (_act.name, *_act.aliases):
        _ALIAS_INDEX[_norm(_key)] = _act


def resolve_act(act: str) -> KnownAct | None:
    """Resolve a lawyer-style act reference («ТК РФ», «Трудовой кодекс») to a known act.

    Exact normalized match first, then a unique-prefix match on canonical names
    («трудовой кодекс» → Трудовой кодекс Российской Федерации). Returns None
    when nothing (or more than one act) matches — the caller decides how to
    report that.
    """
    if not act or not act.strip():
        return None
    key = _norm(act)
    if key in _ALIAS_INDEX:
        return _ALIAS_INDEX[key]
    prefix_hits = {a.nd: a for name, a in _ALIAS_INDEX.items() if name.startswith(key)}
    if len(prefix_hits) == 1:
        return next(iter(prefix_hits.values()))
    return None

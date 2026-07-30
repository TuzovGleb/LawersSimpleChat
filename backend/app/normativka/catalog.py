"""Attribute-search enumeration of the ИПС corpus (the «опись склада»).

One catalog query = «дай все документы вида X в действующем статусе с
текстом». This is the ingest-side discovery mechanism; runtime never touches
it. Result rows are parsed PER BLOCK (``class="list_elem``): a global regex
over the page pairs each nd with the NEIGHBOURING row's title, because every
row's footer icons repeat its nd right before the next row's anchor (verified
live — naive parsing shifted all titles by one).
"""
import logging
import re
from dataclasses import dataclass, replace

from app.normativka.ips_client import IpsClient, build_query

logger = logging.getLogger(__name__)

# Classifier ids (вид документа / статус действия) read from the ИПС's own
# dictionary endpoint: POST ?autocomplete&bpa=cd00000&nclassif=3&area=110 with
# body ``query=<cp1251-urlencoded prefix>`` returns percent-encoded cp1251 TSV
# «label \t id» (nclassif=3 is the вид-документа classifier, 4 — статус).
DOC_KIND_KODEKS = "102000486"       # Кодекс — 25 действующих
DOC_KIND_FZ = "102000505"           # Федеральный закон — ~11 370 действующих с текстом
DOC_KIND_FKZ = "102000506"          # Федеральный конституционный закон
DOC_KIND_ZAKON = "102000484"        # Закон (РФ/РСФСР) — «О защите прав потребителей», «О недрах», «О СМИ»
STATUS_ACTIVE_AMENDED = "102000038"     # «Действует с изменениями»
STATUS_ACTIVE_UNCHANGED = "102000037"   # «Действует без изменений»

# Kind ids -> the ``act_kind`` value stored in the index.
DOC_KIND_TO_ACT_KIND = {
    DOC_KIND_KODEKS: "kodeks",
    DOC_KIND_FZ: "fz",
    DOC_KIND_FKZ: "fkz",
    DOC_KIND_ZAKON: "zakon_rf",
}

PAGE_SIZE = 20  # the server's default; start=N pages through the result set

# Technical acts: their own text is «в статье 5 слова … заменить словами …»,
# useless to a lawyer's question, and the ИПС has already merged their effect
# into the base acts' redactions. Measured on a 320-doc spread across the
# whole ФЗ corpus (2026-07): 91% technical, 9% substantive (≈1000 laws).
# Matched on the act's own name, anchored at the start.
#
# «Об утверждении …» — законы-обёртки, вводившие в действие акты РСФСР
# (Водный/Земельный/Исправительно-трудовой кодексы, КЗоТ). Их собственная
# норма — «Утвердить X и ввести в действие с …», ценности для вопроса юриста
# ноль. Отдельно опасен «Об утверждении Кодекса законов о труде РСФСР»: ИПС
# помечает его «Действует без изменений» и отдаёт вместе с ним ПОЛНЫЙ текст
# советского КЗоТа (258 статей), в тексте ни слова об утрате силы — то есть
# без этого правила индекс выдавал бы отменённое с 2002 г. трудовое право как
# действующее. ВНИМАНИЕ: правило годится только для законов. Для подзаконки
# всё наоборот — «Об утверждении Правил …» несёт сами правила, переносить
# этот пункт на постановления/приказы нельзя.
_TECHNICAL_NAME_RE = re.compile(
    r"^\s*("
    r"о\s+внесени|об\s+изменени|о\s+ратификац|о\s+денонсац|"
    # между «приостановлении» и «действия» вклинивается субъект («о
    # приостановлении Российской Федерацией действия отдельных положений…»)
    r"о\s+приостановлении\b[^,]{0,60}?\bдействия|о\s+прекращении\b[^,]{0,60}?\bдействия|"
    r"о\s+признании\s+утратив|об\s+отмене|"
    # присоединение к конвенциям и принятие уставов/протоколов — та же
    # ратификационная механика; «о принятии» целиком брать нельзя, под него
    # попал бы ФКЗ «О принятии в Российскую Федерацию Республики Крым»
    r"о\s+принятии\b[^,]{0,40}?\b(протокол|устав|конвенц|соглашени)|о\s+присоединении\s+рос|"
    # «об исполнении бюджета» любого фонда (ПФР/ФСС/ФОМС), не только федерального
    r"о\s+выходе\s+из|об\s+исполнении\s+(федерального\s+)?бюджета|"
    r"о\s+продлении\s+срока|об?\s+утверждении"
    r")",
    re.I,
)

_NUMBER_RE = re.compile(r"№\s*([0-9]+[-–]?[A-Za-zА-Яа-яIVXLC]*(?:/[0-9A-Za-zА-Яа-я-]+)?)\s*$")
_DATE_RE = re.compile(r"от\s+(\d{2})\.(\d{2})\.(\d{4})")


def is_technical_act(full_name: str) -> bool:
    """True for amending/ratifying/repealing acts — excluded from the ingest."""
    return bool(_TECHNICAL_NAME_RE.match(full_name or ""))


def parse_number(short_ref: str) -> str:
    """«Федеральный закон от 04.07.2026 № 240-ФЗ» -> «240-ФЗ» (''-safe)."""
    match = _NUMBER_RE.search(short_ref or "")
    return match.group(1).replace("–", "-") if match else ""


def parse_date(short_ref: str) -> str:
    """«… от 04.07.2026 № 240-ФЗ» -> «2026-07-04» ('' when absent)."""
    match = _DATE_RE.search(short_ref or "")
    if not match:
        return ""
    day, month, year = match.groups()
    return f"{year}-{month}-{day}"

_BLOCK_RE = re.compile(r'class="list_elem[^"]*"', re.I)
_ND_RE = re.compile(r"nd=(\d+)")
# No upper length bounds anywhere: statute names run to 500+ characters
# («О внесении изменения в статью 50 Закона … о пенсионном обеспечении лиц,
# проходивших военную службу, службу в органах внутренних дел, …»), and a
# capped pattern drops such a row's name SILENTLY — the act then indexes with
# an empty act_name and becomes unresolvable. Emptiness is checked in code.
_TITLE_RE = re.compile(r'class="bold"[^>]*>\s*([^<]+?)\s*</a>', re.S)
_FULLNAME_RE = re.compile(r'<span class="bold">\s*([^<]+?)\s*</span>', re.S)
_STATUS_RE = re.compile(r'class="tiny_italic_bold">\s*([^<]+?)\s*<', re.S)


@dataclass(frozen=True)
class CatalogEntry:
    nd: str
    short_ref: str   # «Кодекс Российской Федерации от 30.12.2001 № 197-ФЗ»
    full_name: str   # «Трудовой кодекс Российской Федерации»
    status: str      # «Действует с изменениями»
    number: str = ""  # «197-ФЗ» — derived from short_ref; lawyers cite ФЗ by it
    date: str = ""    # «2001-12-30»
    kind: str = ""    # act_kind, filled in by enumerate_acts from the query


def _parse_page(html: str) -> list[CatalogEntry]:
    entries: list[CatalogEntry] = []
    blocks = _BLOCK_RE.split(html)[1:]  # text before the first row is chrome
    for block in blocks:
        nd_match = _ND_RE.search(block)
        title_match = _TITLE_RE.search(block)
        if not nd_match or not title_match:
            continue
        fullname_match = _FULLNAME_RE.search(block, title_match.end())
        status_match = _STATUS_RE.search(block)
        status = status_match.group(1).strip() if status_match else ""
        short_ref = " ".join(title_match.group(1).split())
        entries.append(
            CatalogEntry(
                nd=nd_match.group(1),
                short_ref=short_ref,
                full_name=" ".join(fullname_match.group(1).split()) if fullname_match else "",
                status=status,
                number=parse_number(short_ref),
                date=parse_date(short_ref),
            )
        )
    return entries


def enumerate_acts(
    client: IpsClient,
    *,
    doc_kind_id: str,
    status_ids: tuple[str, ...] = (STATUS_ACTIVE_AMENDED, STATUS_ACTIVE_UNCHANGED),
    max_pages: int = 1000,
) -> list[CatalogEntry]:
    """List every document of a kind in the given statuses, with text present.

    Pages through ``start=N`` until a page adds nothing new. That is the ONLY
    stop condition: stopping early on a short page would let a single
    unparseable row (odd title markup) silently truncate the whole
    enumeration. ``max_pages`` is a runaway guard for the ИПС echoing the same
    page regardless of offset (its known failure mode), not a result cap —
    1000 pages covers the largest corpus (все ФЗ) several times over.
    """
    act_kind = DOC_KIND_TO_ACT_KIND.get(doc_kind_id, "")
    overrides = {"a3": doc_kind_id, "a4": ";".join(status_ids)}
    seen: dict[str, CatalogEntry] = {}
    for page in range(max_pages):
        extra = {"sort": "7"}
        if page:
            extra["start"] = str(page * PAGE_SIZE)
        query = build_query("list_itself", overrides, extra=extra)
        html = client.get_text(query, min_bytes=2000)
        fresh = [e for e in _parse_page(html) if e.nd not in seen]
        if not fresh:
            break
        for entry in fresh:
            seen[entry.nd] = replace(entry, kind=act_kind)
        # ФЗ take ~570 pages — a silent 40-minute loop is indistinguishable
        # from a hang, so report progress as it goes.
        if page and page % 25 == 0:
            logger.info("Каталог: страница %d, документов %d", page, len(seen))
    logger.info("Каталог перечислен", extra={"doc_kind": doc_kind_id, "count": len(seen)})
    return list(seen.values())

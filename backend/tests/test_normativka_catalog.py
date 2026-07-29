"""Catalog list parsing — including the footer-nd trap.

Every result row's FOOTER repeats its nd in the «Сведения о связях» icon links
right before the NEXT row's title anchor, so a naive global regex pairs each
nd with the neighbouring row's title (observed live: «ТК РФ» получал nd КоАП).
The fixture reproduces that structure; the parser must pair per block.
"""
from app.normativka.catalog import _parse_page


def _row(ordinal: int, nd: str, short_ref: str, full_name: str, status: str) -> str:
    return f"""
<table class="list_elem odd" onclick="clickAndGo(this,event)">
<tr><td class="l_top">
<div class="bold">{ordinal}</div>
<span class="tiny_italic_bold"> {status} </span>
</td></tr>
<tr><td class="l_checkbox"><table class="l_name"><tr>
<td class="l"><input type="checkbox" name="check_{nd}" /></td><td>
<div class="l_link">
<a id="link_{ordinal}" href="?docbody=&link_id={ordinal}&nd={nd}&intelsearch=" target="contents" class="bold"> {short_ref} </a>
</div>
<span class="bold">{full_name}</span>
</td></tr></table></td></tr>
<tr><td class="l_bottom">
<ul class="docs_list"><li class='tiny'>Собрание законодательства</li></ul>
<div class="l_pics">
<a href="?docbody=&link_id={ordinal}&nd={nd}&intelsearch="><img src="?pic_doctext.gif" /></a>
<a href="?docbody=&vkart=ref&page=first&link_id={ordinal}&nd={nd}&intelsearch="><img src="?pic_links_forw.gif" /></a>
<a href="?docbody=&vkart=rvref&page=1&link_id={ordinal}&nd={nd}&intelsearch="><img src="?pic_links_back.gif" /></a>
</div>
</td></tr>
</table>
"""


PAGE_HTML = (
    "<html><body><div>chrome, no nd here</div>"
    + _row(1, "102074279", "Кодекс Российской Федерации от 30.12.2001 № 197-ФЗ",
           "Трудовой кодекс Российской Федерации", "Действует c изменениями")
    + _row(2, "102074277", "Кодекс Российской Федерации от 30.12.2001 № 195-ФЗ",
           "Кодекс Российской Федерации об административных правонарушениях", "Действует c изменениями")
    + _row(3, "102038925", "Кодекс Российской Федерации от 29.12.1995 № 223-ФЗ",
           "Семейный кодекс Российской Федерации", "Действует без изменений")
    + "</body></html>"
)


def test_rows_pair_nd_with_their_own_titles():
    entries = _parse_page(PAGE_HTML)
    by_nd = {e.nd: e for e in entries}
    assert len(entries) == 3
    # The footer of row 1 repeats nd=102074279 right before row 2's anchor —
    # a global regex would attribute «КоАП» to the ТК nd. Per-block parsing must not.
    assert by_nd["102074279"].full_name == "Трудовой кодекс Российской Федерации"
    assert by_nd["102074277"].full_name == (
        "Кодекс Российской Федерации об административных правонарушениях"
    )
    assert by_nd["102074279"].short_ref.endswith("№ 197-ФЗ")


def test_status_is_extracted():
    entries = _parse_page(PAGE_HTML)
    statuses = {e.nd: e.status for e in entries}
    assert statuses["102038925"] == "Действует без изменений"


def test_chrome_without_rows_yields_nothing():
    assert _parse_page("<html><body><div>Поиск: ничего</div></body></html>") == []

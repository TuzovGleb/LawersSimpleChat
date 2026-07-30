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


def test_long_names_are_not_truncated_or_dropped():
    # Реальный кейс из корпуса ФЗ (nd=610462737): название на 400+ символов.
    # Капнутый по длине regex терял его МОЛЧА — акт попадал в индекс с пустым
    # act_name и становился неразрешимым по ссылке.
    long_name = (
        "О внесении изменения в статью 50 Закона Российской Федерации "
        '"О пенсионном обеспечении лиц, проходивших военную службу, службу в органах '
        "внутренних дел, Государственной противопожарной службе, органах по контролю за "
        "оборотом наркотических средств и психотропных веществ, учреждениях и органах "
        'уголовно-исполнительной системы, войсках национальной гвардии Российской Федерации, '
        'органах принудительного исполнения Российской Федерации, и их семей"'
    )
    assert len(long_name) > 300
    html = _row(1, "610462737", "Федеральный закон от 04.07.2026 № 225-ФЗ",
                long_name, "Действует без изменений")
    entries = _parse_page(html)
    assert len(entries) == 1
    assert entries[0].full_name == long_name


def test_technical_acts_are_recognized():
    from app.normativka.catalog import is_technical_act
    technical = [
        "О внесении изменений в отдельные законодательные акты Российской Федерации",
        "О внесении изменения в статью 50 Закона Российской Федерации",
        "О ратификации Соглашения между Правительством Российской Федерации",
        "О признании утратившими силу отдельных положений",
        "Об отмене некоторых актов",
        "О приостановлении действия отдельных положений",
        "Об исполнении федерального бюджета за 2024 год",
        # Законы-обёртки, вводившие акты РСФСР. Особый случай — КЗоТ: ИПС
        # помечает его действующим и отдаёт 258 статей отменённого с 2002 г.
        # советского кодекса без единого признака утраты силы.
        "Об утверждении Кодекса законов о труде РСФСР",
        "Об утверждении Земельного кодекса РСФСР",
        "Об утверждении Указов Президиума Верховного Совета РСФСР",
        # ратификационная механика с субъектом внутри названия
        "О приостановлении Российской Федерацией действия отдельных положений Договора",
        "О присоединении Российской Федерации к Найробийской международной конвенции",
        "О принятии Российской Федерацией Устава Международной организации",
        "Об исполнении бюджета Пенсионного фонда Российской Федерации за 2021 год",
    ]
    substantive = [
        "Об обществах с ограниченной ответственностью",
        "О защите прав потребителей",
        "О несостоятельности (банкротстве)",
        "О персональных данных",
        "О контрактной системе в сфере закупок товаров, работ, услуг",
        # ловушка: «изменение» не в начале названия — акт содержательный
        "О порядке рассмотрения обращений об изменении границ",
        # ФКЗ о Крыме начинается с «О принятии», но это не ратификация
        "О принятии в Российскую Федерацию Республики Крым и образовании новых субъектов",
        # бюджет НА год — действующий акт, в отличие от отчёта ОБ исполнении
        "О бюджете Пенсионного фонда Российской Федерации на 2022 год",
    ]
    for name in technical:
        assert is_technical_act(name), name
    for name in substantive:
        assert not is_technical_act(name), name


def test_number_and_date_parsed_from_short_ref():
    from app.normativka.catalog import parse_date, parse_number
    assert parse_number("Федеральный закон от 04.07.2026 № 240-ФЗ") == "240-ФЗ"
    assert parse_number("Закон Российской Федерации от 07.02.1992 № 2300-I") == "2300-I"
    assert parse_number("Федеральный конституционный закон от 21.07.1994 № 1-ФКЗ") == "1-ФКЗ"
    assert parse_number("без номера") == ""
    assert parse_date("Федеральный закон от 04.07.2026 № 240-ФЗ") == "2026-07-04"
    assert parse_date("без даты") == ""


def test_catalog_entry_carries_number_and_date():
    html = _row(1, "610462722", "Федеральный закон от 04.07.2026 № 240-ФЗ",
                "О внесении изменений в отдельные законодательные акты", "Действует без изменений")
    entry = _parse_page(html)[0]
    assert entry.number == "240-ФЗ"
    assert entry.date == "2026-07-04"

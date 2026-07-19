"""Article splitting on the real ИПС document markup."""
from app.normativka.parse import split_articles

# Faithful miniature of the ИПС renderer markup (MHT export / doc_itself):
# headers carry class="H", dotted numbers are superscripts in class="W9",
# amendment marks are class="mark" paragraphs inside the article body.
DOC_HTML = """
<html><body>
<p class="C">НАЛОГОВЫЙ КОДЕКС (фрагмент для теста)</p>
<p class="H">Часть первая</p>
<p class="H">Раздел I. Общие положения</p>
<p class="H">Глава 1. Основные положения</p>
<p class="H">Статья 1. Предмет регулирования</p>
<p>1.&nbsp;Первый пункт статьи.</p>
<p><span class="mark">(В редакции Федерального закона от&nbsp;01.01.2020&nbsp;№&nbsp;1-ФЗ)</span></p>
<p class="H">Статья 2. Отношения, регулируемые кодексом</p>
<p>Текст второй статьи.</p>
<p class="H">ГЛАВА 25<span class="W9">3</span>. ГОСУДАРСТВЕННАЯ ПОШЛИНА</p>
<p class="H">Статья 333<span class="W9">19</span>. Размеры государственной пошлины</p>
<p>Ставки пошлины.</p>
<p class="H">Статья 333<span class="W9">32.1</span>. Составной индекс с точкой</p>
<p>Текст.</p>
<p class="H">Статья 333<span class="W9">34-1</span>. Составной индекс с дефисом</p>
<p>Текст с дефисным индексом.</p>
</body></html>
"""


def test_articles_are_split_with_titles():
    articles = split_articles(DOC_HTML)
    numbers = [a.number for a in articles]
    assert numbers == ["1", "2", "333.19", "333.32.1", "333.34.1"]
    assert articles[0].title == "Предмет регулирования"
    assert "Первый пункт статьи." in articles[0].text


def test_superscript_numbers_are_normalized_to_dotted_form():
    articles = {a.number: a for a in split_articles(DOC_HTML)}
    # «333<W9>19</W9>» -> 333.19; composite indices «32.1» and «34-1» both
    # canonicalize to dotted chains (это и делает КоАП/НК номера искомыми).
    assert "333.19" in articles
    assert articles["333.19"].title.startswith("Размеры государственной пошлины")
    assert "333.32.1" in articles
    assert "333.34.1" in articles


def test_chapter_path_tracks_structure_and_superscript_chapters():
    articles = {a.number: a for a in split_articles(DOC_HTML)}
    assert articles["1"].chapter_path == (
        "Часть первая › Раздел I. Общие положения › Глава 1. Основные положения"
    )
    # Глава 25.3 сбрасывает более глубокие уровни и сама нормализована из W9.
    assert articles["333.19"].chapter_path == "Часть первая › Раздел I. Общие положения › ГЛАВА 25.3. ГОСУДАРСТВЕННАЯ ПОШЛИНА"


def test_amendment_marks_are_kept_in_text():
    articles = {a.number: a for a in split_articles(DOC_HTML)}
    assert "В редакции Федерального закона" in articles["1"].text


def test_tables_between_paragraphs_are_kept_as_article_text():
    # Rate scales (амортизационные группы, ставки сборов) sit in <table>
    # elements BETWEEN <p> blocks; verified lost on НК ч.2 before the fix.
    html = """
    <p class="H">Статья 259. Нормы амортизации</p>
    <p>Вводный абзац.</p>
    <table><tr><td>Амортизационная группа</td><td>Норма (месячная)</td></tr>
    <tr><td>Первая</td><td>14,3</td></tr>
    <tr><td>Вторая</td><td>8,8</td></tr></table>
    <p>Замыкающий абзац.</p>
    <p class="H">Статья 260. Следующая</p>
    <p>Текст.</p>
    """
    articles = {a.number: a for a in split_articles(html)}
    text = articles["259"].text
    assert "Первая | 14,3" in text
    assert "Вторая | 8,8" in text
    # порядок сохранён: таблица между абзацами
    assert text.index("Вводный абзац.") < text.index("Первая | 14,3") < text.index("Замыкающий абзац.")


def test_letter_preceded_superscript_is_a_unit_not_an_index():
    # «650 кг/м<W9>3</W9>» — единица измерения, НЕ номер статьи: точка не
    # добавляется. Цифро-предшествуемый W9 остаётся индексом (346.15).
    html = """
    <p class="H">Статья 193. Ставки акцизов</p>
    <p>плотностью не менее 650 кг/м<span class="W9">3</span> при температуре 20 градусов,
    в порядке статьи 346<span class="W9">15</span> настоящего Кодекса.</p>
    """
    articles = split_articles(html)
    assert "650 кг/м3" in articles[0].text
    assert "статьи 346.15" in articles[0].text


def test_structural_reference_inside_text_does_not_split():
    html = """
    <p class="H">Статья 5. Заголовок</p>
    <p>Согласно статье 4 настоящего Кодекса и Статья-подобному слову текст продолжается.</p>
    <p class="H">Статья 6. Следующая</p>
    <p>Текст.</p>
    """
    articles = split_articles(html)
    assert [a.number for a in articles] == ["5", "6"]
    assert "Согласно статье 4" in articles[0].text

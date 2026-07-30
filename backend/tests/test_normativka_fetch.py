"""Wrapper metadata parsing — both frame-src variants."""
from unittest.mock import MagicMock

from app.normativka.fetch import fetch_act_meta

WRAPPER_DOC_ITSELF = """
<html><head><title>Семейный кодекс Российской Федерации</title></head><body>
<iframe src="?doc_itself=&nd=102038925&page=1&rdk=56"></iframe>
<select><option id="s1o0" value="0">Исходная редакция</option>
<option id="s1o1" value="1">1 - от 15.11.1997 № 140-ФЗ</option></select>
</body></html>
"""

# Большие кодексы (КоАП, НК ч.1, БК): фрейм с encoded-warning префиксом fostr=
WRAPPER_FOSTR = """
<html><head><title>Кодекс Российской Федерации об административных правонарушениях</title></head><body>
<iframe src="?fostr=xO7q8-zl7fIg&nd=102074277&page=1&rdk=962" onload="onDocRefsLoaded();"></iframe>
</body></html>
"""


def _client(html: str) -> MagicMock:
    client = MagicMock()
    client.get_text.return_value = html
    return client


def test_meta_from_doc_itself_wrapper():
    meta = fetch_act_meta(_client(WRAPPER_DOC_ITSELF), "102038925")
    assert meta.current_rdk == "56"
    assert meta.title == "Семейный кодекс Российской Федерации"
    assert ("1", "1 - от 15.11.1997 № 140-ФЗ") in meta.redactions


def test_meta_from_fostr_wrapper():
    meta = fetch_act_meta(_client(WRAPPER_FOSTR), "102074277")
    assert meta.current_rdk == "962"
    assert meta.title.startswith("Кодекс Российской Федерации об административных")
    assert meta.redactions == ()


def test_foreign_nd_rdk_is_not_picked_up():
    # rdk чужого документа в той же обёртке не должен матчиться
    html = WRAPPER_FOSTR.replace("nd=102074277&page=1&rdk=962", "nd=999999&page=1&rdk=111")
    client = _client(html)
    try:
        fetch_act_meta(client, "102074277", parse_retries=1)
        raised = False
    except Exception:
        raised = True
    assert raised


def test_long_document_title_is_parsed():
    # Тот же принцип для обёртки: длинный <title> не должен теряться, иначе
    # сверка заголовка в скрапере провалит акт целиком.
    long_title = "Федеральный закон " + "о очень длинном наименовании " * 15
    html = WRAPPER_FOSTR.replace(
        "Кодекс Российской Федерации об административных правонарушениях", long_title)
    meta = fetch_act_meta(_client(html), "102074277")
    assert meta.title == " ".join(long_title.split())
    assert meta.current_rdk == "962"

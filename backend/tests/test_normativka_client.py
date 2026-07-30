"""IpsClient: повторы при cross-talk (портал кэширует ответы по IP)."""
from unittest.mock import MagicMock

import pytest

from app.normativka.ips_client import IpsClient, IpsError, build_query


def _client(pages: list[bytes]) -> IpsClient:
    client = IpsClient(pause=0.0, retries=3)
    responses = []
    for body in pages:
        response = MagicMock(status_code=200)
        response.content = body
        responses.append(response)
    client._client = MagicMock()
    client._client.get.side_effect = responses
    return client


def test_cross_talk_is_retried_not_fatal():
    # Первый ответ — страница чужого запроса, второй правильный. Обрывать акт
    # нельзя: cross-talk переходящий, из-за него терялось ~2% актов (в т.ч. УПК).
    foreign = "чужая страница nd=999999".encode("cp1251")
    ours = "наша страница nd=102073942 текст".encode("cp1251")
    client = _client([foreign, ours])
    text = client.get_text("docbody=&nd=102073942", echo="nd=102073942")
    assert "наша страница" in text
    assert client._client.get.call_count == 2


def test_persistent_cross_talk_still_fails_loudly():
    foreign = "чужая страница nd=999999".encode("cp1251")
    client = _client([foreign, foreign, foreign])
    with pytest.raises(IpsError, match="устойчивый cross-talk"):
        client.get_text("docbody=&nd=102073942", echo="nd=102073942")


def test_no_echo_means_no_extra_requests():
    client = _client(["любой текст".encode("cp1251")])
    assert client.get_text("list_itself=&x=1") == "любой текст"
    assert client._client.get.call_count == 1


def test_query_values_are_cp1251_percent_encoded():
    # Портал живёт в windows-1251: значения кодируются до urlencode.
    query = build_query("list_itself", {"a3": "102000505"}, extra={"intelsearch": "зерно"})
    assert "a3=102000505" in query
    assert "intelsearch=%E7%E5%F0%ED%EE" in query  # «зерно» в cp1251
    assert "textpres=yes" in query

import os

from app.search.client import OpenSearchConfig, build_opensearch_client


def test_pool_maxsize_default_matches_asyncio_default_executor():
    # min(32, cpu+4) — размер дефолтного ThreadPoolExecutor'а asyncio.to_thread.
    assert OpenSearchConfig().pool_maxsize == min(32, (os.cpu_count() or 1) + 4)


def test_client_urllib3_pool_sized_to_config_and_non_blocking():
    config = OpenSearchConfig(pool_maxsize=7)
    client = build_opensearch_client(config)
    conn = client.transport.connection_pool.connection
    assert conn.pool.pool.maxsize == 7
    # block=False (дефолт): лишний спрос не должен блокировать потоки executor'а.
    assert conn.pool.block is False

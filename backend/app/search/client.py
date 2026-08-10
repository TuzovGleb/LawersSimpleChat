"""OpenSearch client factory."""
import os

from opensearchpy import OpenSearch
from pydantic import BaseModel, Field


class OpenSearchConfig(BaseModel):
    url: str = "http://localhost:9200"
    index_alias: str = "court_decisions"
    top_k: int = Field(default=8, ge=1, le=50)
    # Нормативка (statutes) lives in a sibling index on the same instance.
    normativka_index_alias: str = "legal_acts"
    normativka_top_k: int = Field(default=8, ge=1, le=50)
    # Пул должен вмещать все потоки дефолтного executor'а asyncio.to_thread,
    # иначе urllib3 открывает и выбрасывает соединение на каждый параллельный запрос.
    pool_maxsize: int = Field(default_factory=lambda: min(32, (os.cpu_count() or 1) + 4))


def build_opensearch_client(config: OpenSearchConfig) -> OpenSearch:
    return OpenSearch(
        hosts=[config.url],
        use_ssl=config.url.startswith("https"),
        verify_certs=False,
        ssl_show_warn=False,
        timeout=30,
        max_retries=2,
        retry_on_timeout=True,
        pool_maxsize=config.pool_maxsize,
    )

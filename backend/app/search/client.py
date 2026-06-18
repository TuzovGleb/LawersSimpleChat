"""OpenSearch client factory."""
from opensearchpy import OpenSearch
from pydantic import BaseModel, Field


class OpenSearchConfig(BaseModel):
    url: str = "http://localhost:9200"
    index_alias: str = "court_decisions"
    top_k: int = Field(default=8, ge=1, le=50)


def build_opensearch_client(config: OpenSearchConfig) -> OpenSearch:
    return OpenSearch(
        hosts=[config.url],
        use_ssl=config.url.startswith("https"),
        verify_certs=False,
        ssl_show_warn=False,
        timeout=30,
        max_retries=2,
        retry_on_timeout=True,
    )

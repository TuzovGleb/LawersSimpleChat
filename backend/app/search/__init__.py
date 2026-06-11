"""Court practice full-text search via OpenSearch."""

from app.search.client import OpenSearchConfig, build_opensearch_client
from app.search.search import CourtPracticeSearcher

__all__ = ["CourtPracticeSearcher", "OpenSearchConfig", "build_opensearch_client"]

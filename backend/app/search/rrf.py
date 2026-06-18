"""Reciprocal Rank Fusion for merging multiple ranked result lists."""
from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class RankedDocument:
    doc_id: str
    source: dict
    highlights: list[str]


def reciprocal_rank_fusion(
    result_lists: list[list[RankedDocument]],
    *,
    k: int = 60,
    top_k: int = 8,
) -> list[RankedDocument]:
    if not result_lists:
        return []

    scores: dict[str, float] = defaultdict(float)
    best_doc: dict[str, RankedDocument] = {}

    for results in result_lists:
        for rank, doc in enumerate(results):
            scores[doc.doc_id] += 1.0 / (k + rank + 1)
            if doc.doc_id not in best_doc:
                best_doc[doc.doc_id] = doc

    ranked_ids = sorted(scores, key=scores.get, reverse=True)
    return [best_doc[doc_id] for doc_id in ranked_ids[:top_k]]

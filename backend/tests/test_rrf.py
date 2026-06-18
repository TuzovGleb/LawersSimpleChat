from app.search.rrf import RankedDocument, reciprocal_rank_fusion


def _doc(doc_id: str) -> RankedDocument:
    return RankedDocument(doc_id=doc_id, source={"uid": doc_id}, highlights=[f"snippet-{doc_id}"])


def test_rrf_promotes_documents_present_in_multiple_lists():
    list_a = [_doc("a"), _doc("b"), _doc("c")]
    list_b = [_doc("b"), _doc("a"), _doc("d")]

    merged = reciprocal_rank_fusion([list_a, list_b], top_k=3)

    assert [doc.doc_id for doc in merged] == ["a", "b", "c"]


def test_rrf_deduplicates_by_doc_id():
    list_a = [_doc("x"), _doc("y")]
    list_b = [_doc("x"), _doc("z")]

    merged = reciprocal_rank_fusion([list_a, list_b], top_k=10)

    assert len(merged) == 3
    assert {doc.doc_id for doc in merged} == {"x", "y", "z"}

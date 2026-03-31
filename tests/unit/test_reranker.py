"""Tests for the optional cross-encoder reranker.

The reranker is a pass-through when disabled — it returns the input unchanged.
When enabled, it reorders and trims to top_n.
"""


from contextflow.retrieval.reranker import rerank
from contextflow.retrieval.search_types import SearchResult


def _make_result(chunk_id: str, score: float) -> SearchResult:
    return SearchResult(chunk_id=chunk_id, text=f"text for {chunk_id}", score=score, metadata={})


class TestRerankerDisabledReturnsInput:
    """When use_reranker=False, the reranker is a no-op passthrough."""

    def test_passthrough(self) -> None:
        results = [_make_result("A", 0.9), _make_result("B", 0.8)]
        reranked = rerank(query="test", results=results, use_reranker=False, top_n=3)
        assert reranked == results


class TestRerankerRespectsTopN:
    """When enabled, the reranker should return at most top_n results."""

    def test_top_n(self) -> None:
        results = [_make_result("A", 0.9), _make_result("B", 0.8), _make_result("C", 0.7)]
        reranked = rerank(query="test", results=results, use_reranker=True, top_n=2)
        assert len(reranked) <= 2


class TestRerankerReordersByScore:
    """When enabled, the reranker should return results sorted by score descending."""

    def test_sorted(self) -> None:
        results = [_make_result("A", 0.5), _make_result("B", 0.9), _make_result("C", 0.7)]
        reranked = rerank(query="test", results=results, use_reranker=True, top_n=3)
        scores = [r.score for r in reranked]
        assert scores == sorted(scores, reverse=True)

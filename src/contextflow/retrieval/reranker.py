"""Cross-encoder reranker (optional).

Takes top-k candidates from hybrid search, scores each against the query
using a cross-encoder model, returns re-sorted top results.

For now, the reranker uses the existing scores to re-sort (placeholder).
A real cross-encoder implementation will be added when use_reranker is enabled.
"""

from contextflow.retrieval.search_types import SearchResult


def rerank(
    query: str,
    results: list[SearchResult],
    use_reranker: bool = False,
    top_n: int = 3,
) -> list[SearchResult]:
    """Optionally rerank search results.

    When use_reranker is False, returns the input unchanged (passthrough).
    When True, sorts by score descending and returns top_n.

    Args:
        query: The original query text (used by cross-encoder when enabled).
        results: Candidate search results to rerank.
        use_reranker: Whether to apply reranking.
        top_n: How many results to keep after reranking.

    Returns:
        Reranked (or unchanged) results list.
    """
    if not use_reranker:
        return results

    # Sort by score descending and trim to top_n
    sorted_results = sorted(results, key=lambda r: r.score, reverse=True)
    return sorted_results[:top_n]

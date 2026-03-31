"""Hybrid search — Reciprocal Rank Fusion.

Combine vector search and BM25 results into a single ranked list.
RRF score: sum of 1/(k + rank) across both lists. No score calibration needed.

Why RRF over weighted combination?
- BM25 scores are unbounded, cosine is [0,1] — they can't be directly combined
- RRF uses only rank position, no calibration needed
- Empirically outperforms simple weighted fusion in IR benchmarks
"""

from contextflow.retrieval.search_types import SearchResult


def reciprocal_rank_fusion(
    ranked_lists: list[list[SearchResult]],
    k: int = 60,
) -> list[SearchResult]:
    """Merge multiple ranked lists using Reciprocal Rank Fusion.

    For each document d across all ranked lists:
        RRF_score(d) = sum( 1 / (k + rank(d, list_i)) )
    where rank is 1-indexed.

    Args:
        ranked_lists: List of ranked result lists (e.g., [vector_results, bm25_results]).
        k: Dampening constant (default 60, from the original RRF paper).
           Lower k amplifies top-ranked results, higher k flattens.

    Returns:
        Merged list sorted by RRF score descending.
    """
    scores: dict[str, float] = {}
    results_by_id: dict[str, SearchResult] = {}

    for ranked_list in ranked_lists:
        for rank, result in enumerate(ranked_list, start=1):
            rrf_score = 1.0 / (k + rank)
            scores[result.chunk_id] = scores.get(result.chunk_id, 0.0) + rrf_score
            # Keep the first occurrence's data
            if result.chunk_id not in results_by_id:
                results_by_id[result.chunk_id] = result

    # Build merged results with RRF scores
    merged = []
    for chunk_id, score in scores.items():
        result = results_by_id[chunk_id]
        merged.append(SearchResult(
            chunk_id=chunk_id,
            text=result.text,
            score=score,
            metadata=result.metadata,
        ))

    # Sort by score descending
    merged.sort(key=lambda r: r.score, reverse=True)
    return merged

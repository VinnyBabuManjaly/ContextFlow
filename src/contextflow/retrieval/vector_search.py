"""Vector similarity search.

KNN search against the Redis vector index.
Returns top-k chunks ranked by cosine similarity to the query vector.
"""

import struct

import redis.asyncio as aioredis
from redis.commands.search.query import Query

from contextflow.retrieval.search_types import SearchResult


def _vector_to_bytes(vector: list[float]) -> bytes:
    """Convert a list of floats to binary for Redis KNN query."""
    return struct.pack(f"{len(vector)}f", *vector)


async def vector_search(
    client: aioredis.Redis,
    query_vector: list[float],
    top_k: int = 5,
    similarity_threshold: float = 0.0,
) -> list[SearchResult]:
    """Search the chunk index by vector similarity (cosine distance).

    Uses Redis FT.SEARCH with KNN to find the top-k most similar chunks.
    Results with distance above (1 - similarity_threshold) are excluded.

    Args:
        client: Async Redis client.
        query_vector: The embedded query as a list of floats.
        top_k: Maximum number of results to return.
        similarity_threshold: Minimum similarity (0-1). Chunks below are excluded.

    Returns:
        List of SearchResult ordered by similarity (most similar first).
    """
    query_blob = _vector_to_bytes(query_vector)

    # Build KNN query: "*=>[KNN {top_k} @embedding $query_vec AS vector_distance]"
    q = (
        Query(f"*=>[KNN {top_k} @embedding $query_vec AS vector_distance]")
        .sort_by("vector_distance")
        .return_fields("text", "filename", "section", "vector_distance")
        .dialect(2)
    )

    raw = await client.ft("chunk_index").search(q, query_params={"query_vec": query_blob})

    results: list[SearchResult] = []
    for doc in raw.docs:
        distance = float(doc.vector_distance)
        # Cosine distance: 0 = identical, 2 = opposite
        # Convert to similarity: 1 - distance
        similarity = 1.0 - distance

        if similarity < similarity_threshold:
            continue

        results.append(SearchResult(
            chunk_id=doc.id,
            text=doc.text,
            score=distance,  # store distance for ordering (lower = better)
            metadata={"filename": doc.filename, "section": doc.section},
        ))

    return results

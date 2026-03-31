"""Full-text (BM25) search.

Keyword search against the Redis full-text index.
Catches exact term matches that vector search misses.

Example: a query for "EXPIRE" will match chunks containing that exact command
name, even if the embedding doesn't place it near the query vector.
"""

import redis.asyncio as aioredis
from redis.commands.search.query import Query

from contextflow.retrieval.search_types import SearchResult


def _escape_query(text: str) -> str:
    """Escape special characters in Redis FT.SEARCH query syntax."""
    special = r"@!{}()|-=>[]:;,."
    for char in special:
        text = text.replace(char, f"\\{char}")
    return text


async def text_search(
    client: aioredis.Redis,
    query_text: str,
    top_k: int = 5,
) -> list[SearchResult]:
    """Search the chunk index by BM25 full-text matching.

    Args:
        client: Async Redis client.
        query_text: The raw query string to search for.
        top_k: Maximum number of results to return.

    Returns:
        List of SearchResult ordered by BM25 relevance.
    """
    escaped = _escape_query(query_text)

    q = (
        Query(f"@text:{escaped}")
        .return_fields("text", "filename", "section")
        .paging(0, top_k)
        .dialect(2)
    )

    raw = await client.ft("chunk_index").search(q)

    results: list[SearchResult] = []
    for rank, doc in enumerate(raw.docs):
        results.append(SearchResult(
            chunk_id=doc.id,
            text=doc.text,
            score=float(rank),  # BM25 rank position
            metadata={"filename": doc.filename, "section": doc.section},
        ))

    return results

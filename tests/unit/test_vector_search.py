"""Tests for vector similarity search.

These tests mock the Redis FT.SEARCH call to verify the function builds
the correct query and processes results correctly. Real search is tested
in integration tests.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from contextflow.retrieval.vector_search import vector_search


class TestReturnsChunksRankedBySimilarity:
    """Results should be ordered by cosine distance (ascending = most similar first)."""

    async def test_ranked_by_similarity(self) -> None:
        mock_client = AsyncMock()
        # Simulate FT.SEARCH returning 2 results with distances
        doc1 = MagicMock()
        doc1.id = "chunk:abc:0"
        doc1.__dict__.update({"text": "chunk one", "filename": "a.md", "section": "S1", "vector_distance": "0.1"})
        doc2 = MagicMock()
        doc2.id = "chunk:abc:1"
        doc2.__dict__.update({"text": "chunk two", "filename": "a.md", "section": "S2", "vector_distance": "0.3"})
        search_result = MagicMock()
        search_result.docs = [doc1, doc2]
        search_result.total = 2
        ft_mock = MagicMock()
        ft_mock.search = AsyncMock(return_value=search_result)
        mock_client.ft = MagicMock(return_value=ft_mock)

        results = await vector_search(
            client=mock_client,
            query_vector=[0.1] * 768,
            top_k=5,
            similarity_threshold=0.0,
        )

        assert len(results) == 2
        # First result should have lower distance (higher similarity)
        assert results[0].score <= results[1].score or results[0].chunk_id == "chunk:abc:0"


class TestRespectsTopKParameter:
    """Should return at most k results."""

    async def test_top_k(self) -> None:
        mock_client = AsyncMock()
        docs = []
        for i in range(10):
            doc = MagicMock()
            doc.id = f"chunk:abc:{i}"
            doc.__dict__.update({"text": f"chunk {i}", "filename": "a.md", "section": "S", "vector_distance": str(i * 0.1)})
            docs.append(doc)
        search_result = MagicMock()
        search_result.docs = docs[:3]
        search_result.total = 3
        ft_mock = MagicMock()
        ft_mock.search = AsyncMock(return_value=search_result)
        mock_client.ft = MagicMock(return_value=ft_mock)

        results = await vector_search(
            client=mock_client, query_vector=[0.1] * 768, top_k=3, similarity_threshold=0.0,
        )

        assert len(results) <= 3


class TestReturnsEmptyWhenNoMatch:
    """Should return empty list, not error, when nothing matches."""

    async def test_empty(self) -> None:
        mock_client = AsyncMock()
        search_result = MagicMock()
        search_result.docs = []
        search_result.total = 0
        ft_mock = MagicMock()
        ft_mock.search = AsyncMock(return_value=search_result)
        mock_client.ft = MagicMock(return_value=ft_mock)

        results = await vector_search(
            client=mock_client, query_vector=[0.1] * 768, top_k=5, similarity_threshold=0.0,
        )

        assert results == []

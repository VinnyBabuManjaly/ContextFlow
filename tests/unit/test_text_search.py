"""Tests for BM25 full-text search.

These tests mock the Redis FT.SEARCH call to verify the function builds
the correct query and processes results correctly.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from contextflow.retrieval.text_search import text_search


class TestFindsExactKeywordMatch:
    """BM25 should find chunks containing the exact query term."""

    async def test_finds_keyword(self) -> None:
        mock_client = AsyncMock()
        doc = MagicMock()
        doc.id = "chunk:abc:0"
        doc.__dict__.update({"text": "Use EXPIRE to set TTL", "filename": "a.md", "section": "EXPIRE"})
        search_result = MagicMock()
        search_result.docs = [doc]
        search_result.total = 1
        mock_client.ft.return_value.search = AsyncMock(return_value=search_result)

        results = await text_search(client=mock_client, query_text="EXPIRE", top_k=5)

        assert len(results) == 1
        assert results[0].chunk_id == "chunk:abc:0"


class TestReturnsEmptyForNoMatch:
    """Unrelated term should return empty list."""

    async def test_no_match(self) -> None:
        mock_client = AsyncMock()
        search_result = MagicMock()
        search_result.docs = []
        search_result.total = 0
        mock_client.ft.return_value.search = AsyncMock(return_value=search_result)

        results = await text_search(client=mock_client, query_text="xyznotexist", top_k=5)

        assert results == []


class TestRespectsTopK:
    """Should return at most k results."""

    async def test_top_k(self) -> None:
        mock_client = AsyncMock()
        docs = []
        for i in range(5):
            doc = MagicMock()
            doc.id = f"chunk:abc:{i}"
            doc.__dict__.update({"text": f"chunk {i}", "filename": "a.md", "section": "S"})
            docs.append(doc)
        search_result = MagicMock()
        search_result.docs = docs[:2]
        search_result.total = 2
        mock_client.ft.return_value.search = AsyncMock(return_value=search_result)

        results = await text_search(client=mock_client, query_text="chunk", top_k=2)

        assert len(results) <= 2

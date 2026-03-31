"""Tests for embedding generator.

These tests use a mock — we test the interface contract (correct dimensions,
correct count, error handling), not the actual API call. Real embedding calls
are tested in integration tests.
"""

from unittest.mock import AsyncMock

import pytest

from contextflow.ingestion.embedder import Embedder


@pytest.fixture
def mock_embedder() -> Embedder:
    """Create an embedder with a mocked API call that returns fake vectors."""
    embedder = Embedder(dimension=768)

    async def fake_embed(texts: list[str]) -> list[list[float]]:
        return [[0.1] * 768 for _ in texts]

    embedder._call_api = AsyncMock(side_effect=fake_embed)
    return embedder


class TestEmbedSingleTextReturnsVector:
    """Embedding a single text must return a list of floats with the configured dimension."""

    async def test_returns_vector(self, mock_embedder: Embedder) -> None:
        vector = await mock_embedder.embed_text("How do I set a TTL?")
        assert isinstance(vector, list)
        assert len(vector) == 768
        assert all(isinstance(v, float) for v in vector)


class TestEmbedBatchReturnsMatchingCount:
    """Embedding N texts must return exactly N vectors."""

    async def test_matching_count(self, mock_embedder: Embedder) -> None:
        texts = ["query one", "query two", "query three"]
        vectors = await mock_embedder.embed_texts(texts)
        assert len(vectors) == 3


class TestVectorDimensionMatchesConfig:
    """Every vector must have exactly `dimension` floats."""

    async def test_dimension(self, mock_embedder: Embedder) -> None:
        vectors = await mock_embedder.embed_texts(["test"])
        assert len(vectors[0]) == 768


class TestEmptyTextRaisesError:
    """Embedding an empty string is meaningless — reject it."""

    async def test_rejects_empty(self, mock_embedder: Embedder) -> None:
        with pytest.raises(ValueError, match="empty"):
            await mock_embedder.embed_text("")

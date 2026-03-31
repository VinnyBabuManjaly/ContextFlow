"""Integration tests for the ingestion pipeline.

These tests run the full pipeline against real Redis (Docker) with a mock
embedder (to avoid API calls). They verify that chunks are stored correctly
and are searchable via FT.SEARCH.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import redis.asyncio as aioredis

from contextflow.config import Settings
from contextflow.ingestion.embedder import Embedder
from contextflow.ingestion.pipeline import ingest_pipeline
from contextflow.redis.client import close_redis_client, get_redis_client
from contextflow.redis.indexes import ensure_indexes

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    return Settings(_config_path=config_path)


@pytest.fixture
async def client(settings: Settings) -> aioredis.Redis:
    c = get_redis_client(settings)
    yield c
    await close_redis_client(c)


@pytest.fixture(autouse=True)
async def setup_redis(client: aioredis.Redis, settings: Settings) -> None:
    """Flush DB and create indexes before each test."""
    await client.flushdb()
    await ensure_indexes(client, settings)


@pytest.fixture
def mock_embedder(settings: Settings) -> Embedder:
    """Embedder that returns deterministic fake vectors."""
    embedder = Embedder(dimension=settings.embedding.dimension)
    dim = settings.embedding.dimension

    async def fake_embed(texts: list[str]) -> list[list[float]]:
        vectors = []
        for i, _ in enumerate(texts):
            # Each text gets a slightly different vector so they're distinguishable
            vec = [0.1 + (i * 0.001)] * dim
            vectors.append(vec)
        return vectors

    embedder._call_api = AsyncMock(side_effect=fake_embed)
    return embedder


class TestIngestFileStoresChunksInRedis:
    """After ingesting a file, chunk:* keys should exist in Redis."""

    async def test_stores_chunks(
        self, client: aioredis.Redis, mock_embedder: Embedder, settings: Settings
    ) -> None:
        result = await ingest_pipeline(
            path=FIXTURES / "sample.md",
            redis_client=client,
            embedder=mock_embedder,
            settings=settings,
        )

        assert result.chunks_created >= 2

        # Verify keys exist
        keys = [k async for k in client.scan_iter(match="chunk:*")]
        assert len(keys) == result.chunks_created


class TestIngestIsIdempotent:
    """Ingesting the same file twice should not double the chunk count.
    The doc_id content hash ensures we skip already-indexed documents."""

    async def test_idempotent(
        self, client: aioredis.Redis, mock_embedder: Embedder, settings: Settings
    ) -> None:
        result1 = await ingest_pipeline(
            path=FIXTURES / "sample.md",
            redis_client=client,
            embedder=mock_embedder,
            settings=settings,
        )
        result2 = await ingest_pipeline(
            path=FIXTURES / "sample.md",
            redis_client=client,
            embedder=mock_embedder,
            settings=settings,
        )

        # Second ingest should create 0 new chunks
        assert result2.chunks_created == 0

        # Total keys unchanged
        keys = [k async for k in client.scan_iter(match="chunk:*")]
        assert len(keys) == result1.chunks_created


class TestChunksSearchableAfterIngestion:
    """After ingestion, FT.SEARCH should find the stored chunks."""

    async def test_searchable(
        self, client: aioredis.Redis, mock_embedder: Embedder, settings: Settings
    ) -> None:
        await ingest_pipeline(
            path=FIXTURES / "sample.md",
            redis_client=client,
            embedder=mock_embedder,
            settings=settings,
        )

        # FT.SEARCH with wildcard should return all chunks
        result = await client.ft("chunk_index").search("*")
        assert result.total >= 2


class TestStoredMetadataMatchesSource:
    """The metadata stored in Redis must match the original document."""

    async def test_metadata_matches(
        self, client: aioredis.Redis, mock_embedder: Embedder, settings: Settings
    ) -> None:
        await ingest_pipeline(
            path=FIXTURES / "sample.md",
            redis_client=client,
            embedder=mock_embedder,
            settings=settings,
        )

        # Get the first chunk key
        keys = [k async for k in client.scan_iter(match="chunk:*")]
        first_key = sorted(keys)[0]

        data = await client.hgetall(first_key)
        assert b"text" in data
        assert b"filename" in data
        assert data[b"filename"] == b"sample.md"
        assert b"embedding" in data
        assert b"section" in data

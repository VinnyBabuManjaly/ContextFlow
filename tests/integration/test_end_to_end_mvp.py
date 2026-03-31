"""End-to-end MVP integration test.

Ingest a real document into Redis, then query via the orchestrator.
Uses real Redis + real embedder (mocked API) + mocked LLM.
Verifies the full pipeline: ingest → embed query → search → prompt → answer.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import redis.asyncio as aioredis

from contextflow.api.models import QueryResponse
from contextflow.config import Settings
from contextflow.ingestion.embedder import Embedder
from contextflow.ingestion.pipeline import ingest_pipeline
from contextflow.llm.base import Message
from contextflow.llm.router import LLMRouter
from contextflow.orchestrator import QueryOrchestrator
from contextflow.redis.client import close_redis_client, get_redis_client
from contextflow.redis.indexes import ensure_indexes
from contextflow.retrieval.hybrid import reciprocal_rank_fusion
from contextflow.retrieval.search_types import SearchResult
from contextflow.retrieval.vector_search import vector_search

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PROMPTS = Path(__file__).resolve().parents[2] / "prompts"

# Deterministic vector: all chunks and queries get slightly different vectors
# so that KNN search can distinguish them.
DIM = 768
BASE_VEC = 0.5


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
    await client.flushdb()
    await ensure_indexes(client, settings)


@pytest.fixture
def mock_embedder(settings: Settings) -> Embedder:
    """Embedder that returns deterministic fake vectors.

    Each text gets a unique-ish vector so KNN can rank them.
    The query vector is close to chunk 0's vector.
    """
    embedder = Embedder(dimension=settings.embedding.dimension)
    dim = settings.embedding.dimension

    async def fake_embed(texts: list[str]) -> list[list[float]]:
        vectors = []
        for i, _ in enumerate(texts):
            vec = [BASE_VEC + (i * 0.001)] * dim
            vectors.append(vec)
        return vectors

    embedder._call_api = AsyncMock(side_effect=fake_embed)
    return embedder


@pytest.fixture
def mock_llm_router() -> LLMRouter:
    """LLM router that returns a canned answer with a citation."""
    mock_provider = AsyncMock()
    mock_provider.complete = AsyncMock(
        return_value="Use EXPIRE to set a timeout on a key [chunk:abc:0]."
    )
    return LLMRouter(primary=mock_provider)


class TestIngestThenQueryReturnsGroundedAnswer:
    """Full pipeline: ingest sample.md → query → get answer referencing content."""

    async def test_end_to_end(
        self,
        client: aioredis.Redis,
        mock_embedder: Embedder,
        settings: Settings,
    ) -> None:
        # Ingest
        await ingest_pipeline(
            path=FIXTURES / "sample.md",
            redis_client=client,
            embedder=mock_embedder,
            settings=settings,
        )

        # Build a search function that uses real Redis vector search
        async def search_fn(
            client: aioredis.Redis,
            query_vector: list[float],
        ) -> list[SearchResult]:
            return await vector_search(
                client=client,
                query_vector=query_vector,
                top_k=5,
                similarity_threshold=0.0,
            )

        # Mock LLM to return an answer citing a real chunk
        # First, find what chunks were actually stored
        keys = sorted([k async for k in client.scan_iter(match="chunk:*")])
        first_chunk_id = keys[0].decode() if keys else "chunk:unknown:0"

        mock_provider = AsyncMock()
        mock_provider.complete = AsyncMock(
            return_value=f"EXPIRE sets a timeout on a key [{first_chunk_id}]."
        )
        llm_router = LLMRouter(primary=mock_provider)

        # Query embedder: returns a vector close to the ingested chunks
        query_embedder = Embedder(dimension=settings.embedding.dimension)

        async def query_embed(texts: list[str]) -> list[list[float]]:
            return [[BASE_VEC] * settings.embedding.dimension]

        query_embedder._call_api = AsyncMock(side_effect=query_embed)

        orch = QueryOrchestrator(
            embedder=query_embedder,
            redis_client=client,
            llm_router=llm_router,
            search_fn=search_fn,
            prompt_template_path=PROMPTS / "rag_system_v1.txt",
        )

        result = await orch.query("How does EXPIRE work?")

        assert isinstance(result, QueryResponse)
        assert "EXPIRE" in result.answer
        assert len(result.citations) >= 1
        assert result.citations[0].chunk_id == first_chunk_id
        assert result.latency_ms >= 0


class TestQueryWithNoRelevantDocsRefuses:
    """Query about unrelated topic should return refusal without LLM call."""

    async def test_refusal(
        self,
        client: aioredis.Redis,
        settings: Settings,
    ) -> None:
        # Do NOT ingest anything — Redis is empty

        async def empty_search(
            client: aioredis.Redis,
            query_vector: list[float],
        ) -> list[SearchResult]:
            return await vector_search(
                client=client,
                query_vector=query_vector,
                top_k=5,
                similarity_threshold=0.0,
            )

        mock_provider = AsyncMock()
        mock_provider.complete = AsyncMock(return_value="should not be called")
        llm_router = LLMRouter(primary=mock_provider)

        embedder = Embedder(dimension=settings.embedding.dimension)

        async def fake_embed(texts: list[str]) -> list[list[float]]:
            return [[0.1] * settings.embedding.dimension]

        embedder._call_api = AsyncMock(side_effect=fake_embed)

        orch = QueryOrchestrator(
            embedder=embedder,
            redis_client=client,
            llm_router=llm_router,
            search_fn=empty_search,
            prompt_template_path=PROMPTS / "rag_system_v1.txt",
        )

        result = await orch.query("What is quantum entanglement?")

        assert "No relevant documentation found" in result.answer
        mock_provider.complete.assert_not_called()

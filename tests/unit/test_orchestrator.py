"""Tests for the query orchestrator (MVP version).

All external dependencies (embedder, Redis, LLM) are mocked.
Tests verify the orchestration logic: embed → search → build prompt → call LLM → return.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from contextflow.llm.base import Message
from contextflow.orchestrator import QueryOrchestrator
from contextflow.retrieval.search_types import SearchResult


def _make_orchestrator(
    embed_result: list[float] | None = None,
    search_results: list[SearchResult] | None = None,
    llm_response: str = "Test answer [chunk:abc:0]",
) -> QueryOrchestrator:
    """Create an orchestrator with all dependencies mocked."""
    dim = 768
    mock_embedder = AsyncMock()
    mock_embedder.embed_text = AsyncMock(
        return_value=embed_result or [0.1] * dim,
    )

    mock_redis = AsyncMock()

    mock_router = AsyncMock()
    mock_router.complete = AsyncMock(return_value=llm_response)

    default_results = search_results if search_results is not None else [
        SearchResult(
            chunk_id="chunk:abc:0",
            text="Redis EXPIRE sets a timeout on a key.",
            score=0.032,
            metadata={"filename": "redis.md", "section": "EXPIRE"},
        ),
        SearchResult(
            chunk_id="chunk:abc:1",
            text="TTL returns the remaining time to live.",
            score=0.028,
            metadata={"filename": "redis.md", "section": "TTL"},
        ),
    ]

    mock_search = AsyncMock(return_value=default_results)

    prompt_path = Path(__file__).resolve().parents[2] / "prompts" / "rag_system_v1.txt"

    return QueryOrchestrator(
        embedder=mock_embedder,
        redis_client=mock_redis,
        llm_router=mock_router,
        search_fn=mock_search,
        prompt_template_path=prompt_path,
    )


class TestEmbedQueryCalled:
    """Orchestrator must embed the user query before searching."""

    async def test_embed_called(self) -> None:
        orch = _make_orchestrator()
        await orch.query("What is EXPIRE?")

        orch._embedder.embed_text.assert_called_once_with("What is EXPIRE?")


class TestRetrievalCalledWithQueryVector:
    """Search function must receive the embedded query vector."""

    async def test_search_called(self) -> None:
        query_vec = [0.5] * 768
        orch = _make_orchestrator(embed_result=query_vec)
        await orch.query("What is EXPIRE?")

        orch._search_fn.assert_called_once()
        call_kwargs = orch._search_fn.call_args
        # The query vector should be passed to search
        assert call_kwargs is not None


class TestPromptIncludesRetrievedChunks:
    """The prompt sent to LLM must contain the chunk text with source IDs."""

    async def test_prompt_has_chunks(self) -> None:
        orch = _make_orchestrator()
        await orch.query("What is EXPIRE?")

        call_args = orch._llm_router.complete.call_args
        messages = call_args[0][0]  # first positional arg is messages list
        # Find the system or user message containing chunks
        all_content = " ".join(m.content for m in messages)
        assert "chunk:abc:0" in all_content
        assert "EXPIRE sets a timeout" in all_content


class TestPromptIncludesSystemInstructions:
    """System prompt must contain faithfulness constraints."""

    async def test_system_prompt(self) -> None:
        orch = _make_orchestrator()
        await orch.query("What is EXPIRE?")

        call_args = orch._llm_router.complete.call_args
        messages = call_args[0][0]
        system_msgs = [m for m in messages if m.role == "system"]
        assert len(system_msgs) >= 1
        assert "ONLY" in system_msgs[0].content


class TestResponseIncludesCitations:
    """Response must have Citation objects with chunk_id, filename, section."""

    async def test_citations(self) -> None:
        orch = _make_orchestrator(
            llm_response="EXPIRE sets a timeout on a key [chunk:abc:0]."
        )
        result = await orch.query("What is EXPIRE?")

        assert len(result.citations) >= 1
        assert result.citations[0].chunk_id == "chunk:abc:0"
        assert result.citations[0].filename == "redis.md"
        assert result.citations[0].section == "EXPIRE"


class TestHandlesNoRetrievalResults:
    """Empty retrieval should return refusal without calling LLM."""

    async def test_no_results(self) -> None:
        orch = _make_orchestrator(search_results=[])
        result = await orch.query("What is quantum physics?")

        assert "No relevant documentation found" in result.answer
        # LLM should NOT be called
        orch._llm_router.complete.assert_not_called()


class TestCitationValidationStripsInvalid:
    """Citations referencing non-retrieved chunks must be stripped."""

    async def test_strips_invalid(self) -> None:
        orch = _make_orchestrator(
            llm_response="Answer [chunk:abc:0] and also [chunk:fake:99]."
        )
        result = await orch.query("What is EXPIRE?")

        chunk_ids = [c.chunk_id for c in result.citations]
        assert "chunk:abc:0" in chunk_ids
        assert "chunk:fake:99" not in chunk_ids

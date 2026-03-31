"""Query orchestrator — the main pipeline.

Ties all layers together in sequence:
1. Embed query
2. [SKIP - cache lookup, Phase 7]
3. Retrieve session history
4. [SKIP - long-term facts, Phase 8]
5. Hybrid search (vector + BM25 + RRF)
6. [SKIP - reranker, optional]
7. Build prompt
8. Call LLM
9. Post-process (validate citations, save session turns)
10. Return answer + citations
"""

import re
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis

from contextflow.api.models import Citation, QueryResponse
from contextflow.ingestion.embedder import Embedder
from contextflow.llm.base import Message
from contextflow.llm.router import LLMRouter
from contextflow.memory.session import SessionMemory, Turn
from contextflow.retrieval.search_types import SearchResult

NO_RESULTS_MESSAGE = "No relevant documentation found for this query."
CITATION_PATTERN = re.compile(r"\[([^\]]+)\]")

SearchFn = Callable[..., Coroutine[Any, Any, list[SearchResult]]]


def _format_chunks(results: list[SearchResult]) -> str:
    """Format retrieved chunks for injection into the prompt."""
    if not results:
        return ""

    lines: list[str] = []
    for result in results:
        filename = result.metadata.get("filename", "unknown")
        section = result.metadata.get("section", "")
        header = f"[source: {result.chunk_id} | {filename} § {section}]"
        lines.append(f"{header}\n{result.text}\n")
    return "\n".join(lines)


def _format_history(turns: list[Turn]) -> str:
    """Format session history for prompt injection."""
    if not turns:
        return ""

    lines: list[str] = []
    for turn in turns:
        prefix = "User" if turn.role == "user" else "Assistant"
        lines.append(f"{prefix}: {turn.content}")
    return "\n".join(lines)


def _extract_citations(
    answer: str,
    retrieved_ids: set[str],
    results_by_id: dict[str, SearchResult],
) -> list[Citation]:
    """Extract and validate citation references from the LLM answer."""
    found_ids = CITATION_PATTERN.findall(answer)
    citations: list[Citation] = []
    seen: set[str] = set()

    for chunk_id in found_ids:
        if chunk_id in retrieved_ids and chunk_id not in seen:
            seen.add(chunk_id)
            result = results_by_id[chunk_id]
            citations.append(Citation(
                chunk_id=chunk_id,
                filename=result.metadata.get("filename", "unknown"),
                section=result.metadata.get("section", ""),
            ))

    return citations


class QueryOrchestrator:
    """Orchestrates the RAG query pipeline.

    Args:
        embedder: Text embedder for query vectorization.
        redis_client: Async Redis client.
        llm_router: LLM router for completions.
        search_fn: Async search function (hybrid search).
        prompt_template_path: Path to the RAG system prompt template.
        session_memory: Optional session memory for multi-turn conversations.
    """

    def __init__(
        self,
        embedder: Embedder,
        redis_client: aioredis.Redis,
        llm_router: LLMRouter,
        search_fn: SearchFn,
        prompt_template_path: Path,
        session_memory: SessionMemory | None = None,
    ) -> None:
        self._embedder = embedder
        self._redis_client = redis_client
        self._llm_router = llm_router
        self._search_fn = search_fn
        self._prompt_template = prompt_template_path.read_text()
        self._session_memory = session_memory

    async def query(
        self,
        query_text: str,
        session_id: str | None = None,
    ) -> QueryResponse:
        """Run the full RAG pipeline for a user query.

        Args:
            query_text: The user's question.
            session_id: Optional session ID for multi-turn context.

        Returns:
            QueryResponse with answer, citations, and metadata.
        """
        start = time.monotonic()

        # Step 1: Embed query
        query_vector = await self._embedder.embed_text(query_text)

        # Step 3: Retrieve session history
        history_text = ""
        if session_id and self._session_memory:
            turns = await self._session_memory.get_recent_turns(session_id)
            history_text = _format_history(turns)

        # Step 5: Search
        results = await self._search_fn(
            client=self._redis_client,
            query_vector=query_vector,
        )

        # Empty retrieval → refuse without LLM call
        if not results:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return QueryResponse(
                answer=NO_RESULTS_MESSAGE,
                citations=[],
                latency_ms=elapsed_ms,
                session_id=session_id,
            )

        # Step 7: Build prompt
        chunks_text = _format_chunks(results)
        system_content = self._prompt_template.format(
            chunks=chunks_text,
            history=history_text,
            query=query_text,
        )
        messages = [
            Message(role="system", content=system_content),
            Message(role="user", content=query_text),
        ]

        # Step 8: Call LLM
        answer = await self._llm_router.complete(messages)

        # Step 9: Validate citations
        retrieved_ids = {r.chunk_id for r in results}
        results_by_id = {r.chunk_id: r for r in results}
        citations = _extract_citations(str(answer), retrieved_ids, results_by_id)

        # Step 9b: Save session turns
        if session_id and self._session_memory:
            await self._session_memory.add_turn(session_id, "user", query_text)
            await self._session_memory.add_turn(session_id, "assistant", str(answer))

        # Step 10: Return
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return QueryResponse(
            answer=str(answer),
            citations=citations,
            latency_ms=elapsed_ms,
            session_id=session_id,
        )

"""Tests for Pydantic request/response schemas.

These tests define the contract for every data model that crosses a system
boundary — API requests, API responses, and internal data transfer objects.
Each model must validate its inputs, reject bad data, and serialize cleanly.
"""

import json

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# The imports below will fail until api/models.py is implemented.
# That is intentional — this is the RED phase of TDD.
# ---------------------------------------------------------------------------
from contextflow.api.models import (
    CacheEntry,
    ChunkMetadata,
    Citation,
    IngestRequest,
    MemoryFact,
    MetricsResponse,
    QueryRequest,
    QueryResponse,
)


class TestQueryRequestRejectsEmptyQuery:
    """The query field is the single required input to the system. An empty
    string means there's nothing to search for — reject it immediately
    rather than sending an empty embedding to Redis."""

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValidationError):
            QueryRequest(query="")

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(ValidationError):
            QueryRequest(query="   ")


class TestQueryRequestAcceptsOptionalSessionId:
    """session_id enables multi-turn conversations but is not required for
    a single-shot query. It must default to None so first-time users don't
    need to manage sessions."""

    def test_session_id_defaults_to_none(self) -> None:
        request = QueryRequest(query="How do I set a TTL?")
        assert request.session_id is None

    def test_session_id_accepted_when_provided(self) -> None:
        request = QueryRequest(query="How do I set a TTL?", session_id="sess_abc123")
        assert request.session_id == "sess_abc123"


class TestQueryResponseSerialization:
    """QueryResponse is what the API returns to the client. It must serialize
    to valid JSON including nested Citation objects, so clients can parse it
    reliably."""

    def test_serializes_to_valid_json(self) -> None:
        response = QueryResponse(
            answer="Use the EXPIRE command to set a timeout on a key.",
            citations=[
                Citation(
                    chunk_id="chunk_abc123_0",
                    filename="redis-commands.md",
                    section="EXPIRE",
                )
            ],
            from_cache=False,
            latency_ms=1240,
            session_id="sess_abc123",
        )

        # Act — serialize to JSON string, then parse back
        json_str = response.model_dump_json()
        parsed = json.loads(json_str)

        # Assert — key fields survive the round-trip
        assert parsed["answer"] == "Use the EXPIRE command to set a timeout on a key."
        assert len(parsed["citations"]) == 1
        assert parsed["citations"][0]["chunk_id"] == "chunk_abc123_0"
        assert parsed["from_cache"] is False
        assert parsed["latency_ms"] == 1240


class TestCitationHasRequiredFields:
    """Every citation must identify which chunk it came from and where that
    chunk lives in the original document. Without these fields, the user
    cannot verify the answer against the source."""

    def test_requires_chunk_id(self) -> None:
        with pytest.raises(ValidationError):
            Citation(filename="redis-commands.md", section="EXPIRE")  # type: ignore[call-arg]

    def test_requires_filename(self) -> None:
        with pytest.raises(ValidationError):
            Citation(chunk_id="chunk_abc123_0", section="EXPIRE")  # type: ignore[call-arg]

    def test_requires_section(self) -> None:
        with pytest.raises(ValidationError):
            Citation(chunk_id="chunk_abc123_0", filename="redis-commands.md")  # type: ignore[call-arg]

    def test_valid_citation(self) -> None:
        citation = Citation(
            chunk_id="chunk_abc123_0",
            filename="redis-commands.md",
            section="EXPIRE",
        )
        assert citation.chunk_id == "chunk_abc123_0"
        assert citation.filename == "redis-commands.md"
        assert citation.section == "EXPIRE"


class TestIngestRequestValidatesPath:
    """The ingest endpoint accepts a file or directory path. An empty path
    means there's nothing to ingest — reject it rather than failing deep
    in the file loader."""

    def test_rejects_empty_path(self) -> None:
        with pytest.raises(ValidationError):
            IngestRequest(path="")

    def test_accepts_valid_path(self) -> None:
        request = IngestRequest(path="/docs/redis-guide.md")
        assert request.path == "/docs/redis-guide.md"


class TestChunkMetadataRequiresDocId:
    """doc_id is the content hash that enables idempotent re-ingestion.
    Without it, we can't detect duplicate chunks or track which document
    a chunk belongs to."""

    def test_rejects_missing_doc_id(self) -> None:
        with pytest.raises(ValidationError):
            ChunkMetadata(  # type: ignore[call-arg]
                filename="redis-commands.md",
                section="EXPIRE",
                chunk_index=0,
                token_count=450,
                char_offset=0,
            )

    def test_accepts_complete_metadata(self) -> None:
        meta = ChunkMetadata(
            doc_id="abc123hash",
            filename="redis-commands.md",
            section="EXPIRE",
            chunk_index=0,
            token_count=450,
            char_offset=0,
        )
        assert meta.doc_id == "abc123hash"


class TestCacheEntryRequiresAnswer:
    """A cache entry without an answer is useless — it would be a cache hit
    that returns nothing. The answer field must be required."""

    def test_rejects_missing_answer(self) -> None:
        with pytest.raises(ValidationError):
            CacheEntry(  # type: ignore[call-arg]
                query_text="How to set TTL?",
                source_chunks=["chunk_abc123_0"],
                model_used="gemini-2.0-flash",
            )

    def test_accepts_complete_entry(self) -> None:
        entry = CacheEntry(
            query_text="How to set TTL?",
            answer="Use the EXPIRE command.",
            source_chunks=["chunk_abc123_0"],
            model_used="gemini-2.0-flash",
        )
        assert entry.answer == "Use the EXPIRE command."


class TestMemoryFactConfidenceRange:
    """Confidence scores outside 0.0-1.0 are meaningless. Below 0.0 is
    impossible, above 1.0 suggests a bug in the extraction logic."""

    def test_rejects_negative_confidence(self) -> None:
        with pytest.raises(ValidationError):
            MemoryFact(fact_text="User prefers Python", confidence=-0.1)

    def test_rejects_confidence_above_one(self) -> None:
        with pytest.raises(ValidationError):
            MemoryFact(fact_text="User prefers Python", confidence=1.5)

    def test_accepts_valid_confidence(self) -> None:
        fact = MemoryFact(fact_text="User prefers Python", confidence=0.85)
        assert fact.confidence == 0.85


class TestMetricsResponseHasAllSections:
    """The /metrics endpoint must return all four sections so monitoring
    dashboards can rely on a consistent schema."""

    def test_has_all_sections(self) -> None:
        response = MetricsResponse(
            cache={"total_queries": 100, "cache_hits": 25, "hit_rate_percent": 25.0},
            retrieval={"avg_retrieval_latency_ms": 23, "avg_chunks_returned": 4.2},
            llm={"avg_generation_latency_ms": 1840, "total_tokens_used": 48200},
            memory={"redis_used_memory_mb": 87, "total_chunks_indexed": 11420},
        )
        assert "total_queries" in response.cache
        assert "avg_retrieval_latency_ms" in response.retrieval
        assert "avg_generation_latency_ms" in response.llm
        assert "redis_used_memory_mb" in response.memory

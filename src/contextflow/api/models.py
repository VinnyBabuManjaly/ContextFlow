"""Pydantic request/response schemas.

Every data model that crosses a system boundary lives here — API requests,
API responses, and internal data transfer objects. Each model validates its
inputs at construction time so invalid data never reaches business logic.
"""

from pydantic import BaseModel, field_validator

# ---------------------------------------------------------------------------
# Internal data transfer objects
# ---------------------------------------------------------------------------

class ChunkMetadata(BaseModel):
    """Metadata attached to every chunk during ingestion.
    doc_id is a SHA-256 content hash enabling idempotent re-ingestion."""

    doc_id: str
    filename: str
    section: str
    chunk_index: int
    token_count: int
    char_offset: int
    version: str = ""
    indexed_at: int = 0


class CacheEntry(BaseModel):
    """A cached query-answer pair stored in Redis."""

    query_text: str
    answer: str
    source_chunks: list[str]
    model_used: str
    created_at: int = 0
    hit_count: int = 0


class MemoryFact(BaseModel):
    """A user fact extracted from conversation for long-term personalization."""

    fact_text: str
    confidence: float

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {v}")
        return v


# ---------------------------------------------------------------------------
# API request models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    """POST /query request body."""

    query: str
    session_id: str | None = None
    filters: dict[str, str] | None = None
    stream: bool = False

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("query must not be empty or whitespace-only")
        return v


class IngestRequest(BaseModel):
    """POST /ingest request body."""

    path: str

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("path must not be empty")
        return v


# ---------------------------------------------------------------------------
# API response models
# ---------------------------------------------------------------------------

class Citation(BaseModel):
    """A single source citation linking a claim to a specific chunk."""

    chunk_id: str
    filename: str
    section: str


class QueryResponse(BaseModel):
    """POST /query response body."""

    answer: str
    citations: list[Citation] = []
    from_cache: bool = False
    latency_ms: int = 0
    session_id: str | None = None


class IngestResponse(BaseModel):
    """POST /ingest response body."""

    chunks_created: int
    filename: str


class SessionHistory(BaseModel):
    """GET /session/{id}/history response body."""

    session_id: str
    turns: list[dict[str, str]] = []


class HealthResponse(BaseModel):
    """GET /health response body."""

    status: str
    redis_connected: bool = False
    indexes_exist: bool = False


class MetricsResponse(BaseModel):
    """GET /metrics response body. Each section is a flexible dict so the
    metrics tracker can evolve without schema changes."""

    cache: dict[str, float | int]
    retrieval: dict[str, float | int]
    llm: dict[str, float | int]
    memory: dict[str, float | int]

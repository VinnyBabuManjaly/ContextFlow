# ContextFlow Implementation Plan

> **Status**: Blueprint complete, implementation pending.
> **Created**: 2026-03-28
> **Source documents**: All decisions, thresholds, and schemas referenced below are drawn from the existing design docs listed in [Appendix A](#appendix-a-reference-documents).

---

## 1. Current State of the Repository

| Category | Status |
|----------|--------|
| Design documents | 5 comprehensive docs: system design, RAG deep-dive, evaluation strategy, prompt templates, config reference |
| Module structure | 35 Python source files scaffolded across 8 packages |
| Implementation code | **Zero** -- every `.py` file contains only a docstring describing its purpose |
| Infrastructure | `docker-compose.yml` (Redis Stack), `pyproject.toml`, `Makefile`, `.env.example`, `.gitignore` all ready |
| Tests | `tests/conftest.py` (stub), `tests/unit/.gitkeep`, `tests/integration/.gitkeep`, `tests/eval/eval_set.json` (empty array) |
| Dependencies | Declared in `pyproject.toml`: fastapi, uvicorn, redis[hiredis], openai, tiktoken, click, pydantic, pydantic-settings, pyyaml + dev/local extras |

### Module Map

```
src/contextflow/
├── __init__.py
├── main.py                  # CLI entrypoint (Click)
├── config.py                # Settings from config.yaml + env vars
├── orchestrator.py          # 10-step query pipeline
├── ingestion/
│   ├── loader.py            # Read files (md, txt, pdf)
│   ├── chunker.py           # Recursive splitting, 500 tokens, 50 overlap
│   └── embedder.py          # OpenAI or local sentence-transformers
├── retrieval/
│   ├── vector_search.py     # KNN against Redis FLAT/HNSW index
│   ├── text_search.py       # BM25 full-text via FT.SEARCH
│   ├── hybrid.py            # Reciprocal Rank Fusion
│   └── reranker.py          # Cross-encoder re-scoring (optional)
├── cache/
│   └── semantic_cache.py    # Pre-LLM cosine check, post-LLM cache write
├── memory/
│   ├── session.py           # Redis Streams per session_id
│   └── long_term.py         # Fact extraction + vector retrieval
├── llm/
│   ├── base.py              # Abstract LLM interface
│   ├── router.py            # Provider selection + fallback
│   ├── openai.py            # OpenAI provider
│   ├── gemini.py            # Gemini provider
│   └── ollama.py            # Ollama provider
├── api/
│   ├── app.py               # FastAPI lifespan, mount routes
│   ├── models.py            # Pydantic request/response schemas
│   └── routes/
│       ├── query.py         # POST /query, GET /query/stream
│       ├── ingest.py        # POST /ingest
│       ├── session.py       # POST/DELETE/GET /session
│       └── metrics.py       # GET /metrics, GET /health
└── redis/
    ├── client.py            # Async connection pool
    └── indexes.py           # FT.CREATE schemas (chunk, cache, memory)
```

---

## 2. Guiding Principles

### 2.1 Bottom-Up Construction

Each phase produces testable, working code before the next phase builds on it. No phase requires mocking a component that should already exist. Dependencies flow strictly downward.

### 2.2 Strict TDD (Red -> Green -> Refactor)

Every phase begins with test files. Tests are written first, confirmed to fail for the right reason, then the minimum implementation is written to make them pass. No production code exists without a failing test that justifies it.

**Test structure** (from `CLAUDE.md`):
- **Arrange** -- set up inputs and dependencies
- **Act** -- call the single thing being tested
- **Assert** -- verify the outcome (one logical assertion per test)
- Test names describe behavior: `test_returns_cached_result_when_similar_query_exists`

### 2.3 MVP as the North Star

The first 5 phases collectively produce a **Minimum Viable Pipeline**: ingest documentation -> query -> grounded answer with citations. Every sequencing decision optimizes for reaching this milestone as early as possible. Phases 6-10 layer enhancements onto the working core.

### 2.4 Key Technical Constraints (from System Design Doc)

- **Redis Stack is the only backend** -- vectors, cache, sessions, metrics, memory all in Redis
- **All I/O is async** -- no blocking calls inside async functions
- **All function signatures have type annotations** -- parameters and return types
- **Max function length ~30 lines** -- if it's longer, it's doing too much
- **Config loaded once at startup** -- not re-read per request
- **Secrets from env only** -- fail on startup if required secrets are missing
- **Structured logging** -- JSON format, right level, enough context per entry
- **Graceful degradation** -- every non-core component (cache, session, long-term memory) is independently bypassable

---

## 3. Phased Implementation Plan

---

### Phase 1: Configuration and Data Types

**Goal**: A fully validated `Settings` object that loads from `config.yaml` and environment variables, plus all Pydantic request/response schemas used across the system.

**Why first**: Every subsequent module imports `Settings`. Building it first means no module ever needs to hardcode a value or accept untyped configuration. The Pydantic models are pure data classes with no I/O dependencies, making them trivially testable and consumed everywhere downstream.

#### Files to Implement

| File | What it does |
|------|-------------|
| `src/contextflow/config.py` | Pydantic Settings classes: `IngestionSettings`, `EmbeddingSettings`, `RetrievalSettings`, `CacheSettings`, `SessionSettings`, `LongTermMemorySettings`, `LLMSettings`, `RedisSettings`, `ServerSettings`, `LoggingSettings`, and root `Settings`. Load from `config.yaml` via PyYAML, overlay env vars via `pydantic-settings`. |
| `src/contextflow/api/models.py` | Pydantic models: `QueryRequest`, `QueryResponse`, `Citation`, `IngestRequest`, `IngestResponse`, `SessionHistory`, `MetricsResponse`, `HealthResponse`, `ChunkMetadata`, `CacheEntry`, `MemoryFact`. |
| `config.yaml` (new, project root) | All defaults from the config reference doc (`docs/05-Config/00-config-reference.md`). |

#### Settings Class Structure

Derived from the config reference (`docs/05-Config/00-config-reference.md` Section 2):

```python
class IngestionSettings:
    chunk_size: int = 500           # 100-1500 tokens
    chunk_overlap: int = 50         # 0-200 tokens
    chunking_strategy: str = "recursive"  # "recursive" | "fixed" | "heading"
    supported_formats: list[str] = ["md", "txt", "pdf"]

class EmbeddingSettings:
    provider: str = "gemini"        # "gemini" | "openai" | "local"
    model: str = "text-embedding-004"
    dimension: int = 768            # must match model output
    batch_size: int = 100           # 1-2048

class RetrievalSettings:
    top_k: int = 5                  # 1-20
    similarity_threshold: float = 0.7  # 0.0-1.0
    fusion_method: str = "rrf"      # "rrf" | "weighted"
    rrf_k: int = 60                 # 1-100
    use_reranker: bool = False
    rerank_top_n: int = 3           # 1-10

class CacheSettings:
    enabled: bool = True
    distance_threshold: float = 0.10  # 0.01-0.30
    ttl_seconds: int = 604800       # 7 days
    max_entries: int = 10000
    require_citations: bool = True

class SessionSettings:
    max_turns: int = 100
    context_window_turns: int = 10
    ttl_seconds: int = 86400        # 24 hours
    summarize_after_turns: int = 50

class LongTermMemorySettings:
    enabled: bool = True
    ttl_days: int = 30
    min_confidence: float = 0.6
    max_facts_injected: int = 5
    retrieval_threshold: float = 0.7

class LLMSettings:
    provider: str = "gemini"
    model: str = "gemini-2.0-flash"
    base_url: str | None = None
    max_tokens: int = 1024
    temperature: float = 0.1
    stream: bool = True
    timeout_seconds: int = 30
    fallback: FallbackSettings | None = None

class RedisSettings:
    url: str = "redis://localhost:6379"
    max_connections: int = 10
    index_type: str = "FLAT"        # "FLAT" | "HNSW"
    hnsw_m: int = 16
    hnsw_ef_construction: int = 200
    hnsw_ef_runtime: int = 10

class ServerSettings:
    host: str = "127.0.0.1"
    port: int = 8000
    workers: int = 1

class LoggingSettings:
    level: str = "INFO"
    format: str = "json"

class MetricsSettings:
    enabled: bool = True

class Settings:
    ingestion: IngestionSettings
    embedding: EmbeddingSettings
    retrieval: RetrievalSettings
    cache: CacheSettings
    session: SessionSettings
    long_term_memory: LongTermMemorySettings
    llm: LLMSettings
    redis: RedisSettings
    server: ServerSettings
    logging: LoggingSettings
    metrics: MetricsSettings
```

**Precedence** (from config reference Section 1): environment variable > `.env` file > `config.yaml` defaults.

#### Tests to Write First

**`tests/unit/test_config.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_loads_defaults_from_yaml` | Given a minimal config.yaml, all default values are populated correctly |
| `test_env_var_overrides_yaml` | `REDIS_URL` env var overrides the yaml `redis.url` value |
| `test_fails_on_missing_required_secret` | If `GEMINI_API_KEY` is required (provider=gemini) and absent, startup raises a clear error |
| `test_validates_chunk_size_range` | `chunk_size` outside 100-1500 raises `ValidationError` |
| `test_validates_embedding_dimension_positive` | `dimension` of 0 or negative is rejected |
| `test_validates_cache_threshold_range` | `distance_threshold` outside 0.01-0.30 is rejected |
| `test_validates_temperature_range` | `temperature` outside 0.0-2.0 is rejected |
| `test_config_loaded_once` | Calling `get_settings()` twice returns the same object (singleton) |

**`tests/unit/test_models.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_query_request_rejects_empty_query` | `QueryRequest(query="")` raises `ValidationError` |
| `test_query_request_accepts_optional_session_id` | `session_id` field is optional, defaults to None |
| `test_query_response_serialization` | `QueryResponse` with citations serializes to valid JSON |
| `test_citation_has_required_fields` | `Citation` requires `chunk_id`, `filename`, `section` |
| `test_ingest_request_validates_path` | `IngestRequest` validates the `path` field is not empty |
| `test_chunk_metadata_requires_doc_id` | `ChunkMetadata` without `doc_id` raises error |
| `test_cache_entry_requires_answer` | `CacheEntry` without `answer` raises error |
| `test_memory_fact_confidence_range` | `MemoryFact` rejects `confidence` outside 0.0-1.0 |
| `test_metrics_response_has_all_sections` | `MetricsResponse` has `cache`, `retrieval`, `llm`, `memory` fields |

#### Dependencies

None -- this is the foundation.

#### Done When

`pytest tests/unit/test_config.py tests/unit/test_models.py` all green. The `Settings` object loads, validates, and rejects bad input. All Pydantic schemas serialize and deserialize correctly.

---

### Phase 2: Redis Client and Index Schemas

**Goal**: An async Redis connection pool that connects on startup and disconnects cleanly, plus idempotent `FT.CREATE` commands for all three search indexes.

**Why second**: Redis is the single backend for everything. The client is a dependency injected into every module that does I/O. Building it now means Phase 3+ can use real Redis in integration tests rather than mocking.

#### Files to Implement

| File | What it does |
|------|-------------|
| `src/contextflow/redis/client.py` | `get_redis_client(settings) -> redis.asyncio.Redis` -- creates async connection pool from `settings.redis.url` with `max_connections`. `close_redis_client(client)` -- drains and closes the pool. |
| `src/contextflow/redis/indexes.py` | `ensure_indexes(client, settings)` -- creates three FT.CREATE indexes if they don't exist. Each wrapped in try/except for "Index already exists" (`ResponseError`). |
| `tests/conftest.py` | Shared fixtures: `settings` (test-specific Settings), `redis_client` (connects to test Redis), `clean_redis` (FLUSHDB before each integration test). |

#### Index Schemas

From the system design doc (`docs/02-System-Design/00-system-design.md` Section 5):

```
# chunk_index -- for document chunks (vector + full-text + metadata)
FT.CREATE chunk_index ON HASH PREFIX 1 chunk:
  SCHEMA
    text           TEXT                    # BM25 full-text search
    embedding      VECTOR {FLAT|HNSW} 6
                     TYPE FLOAT32
                     DIM {settings.embedding.dimension}
                     DISTANCE_METRIC COSINE
    filename       TAG                     # metadata filter
    section        TEXT                    # section heading search
    version        NUMERIC                # version filter

# cache_index -- for semantic cache lookup
FT.CREATE cache_index ON HASH PREFIX 1 cache:
  SCHEMA
    query_vector   VECTOR FLAT 6
                     TYPE FLOAT32
                     DIM {settings.embedding.dimension}
                     DISTANCE_METRIC COSINE

# memory_index -- for long-term user facts
FT.CREATE memory_index ON HASH PREFIX 1 memory:
  SCHEMA
    fact_vector    VECTOR FLAT 6
                     TYPE FLOAT32
                     DIM {settings.embedding.dimension}
                     DISTANCE_METRIC COSINE
```

The `chunk_index` uses `FLAT` or `HNSW` based on `settings.redis.index_type`. The design decision (Section 3.3) is to start with FLAT (exact search at ~11K vectors is fast enough, ~2-5ms) and switch to HNSW only when the index grows beyond ~50K vectors.

#### Tests to Write First

**`tests/unit/test_redis_client.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_get_client_returns_async_redis` | Factory returns a `redis.asyncio.Redis` instance |
| `test_client_uses_configured_url` | Connection uses `settings.redis.url` |
| `test_client_pool_size_matches_config` | `max_connections` matches `settings.redis.max_connections` |

**`tests/unit/test_indexes.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_chunk_index_schema_has_required_fields` | FT.CREATE command includes text TEXT, embedding VECTOR, filename TAG, section TEXT, version NUMERIC |
| `test_index_creation_is_idempotent` | Calling `ensure_indexes()` twice does not raise an error |
| `test_index_uses_configured_dimension` | Vector dimension matches `settings.embedding.dimension` |
| `test_index_type_matches_config` | FLAT or HNSW matches `settings.redis.index_type` |

**`tests/integration/test_redis_connection.py`** (requires Docker Redis):

| Test | What it verifies |
|------|-----------------|
| `test_ping_succeeds` | `client.ping()` returns True |
| `test_set_get_roundtrip` | Basic SET/GET works |
| `test_creates_chunk_index` | After `ensure_indexes()`, `FT.INFO chunk_index` succeeds |
| `test_creates_cache_index` | `FT.INFO cache_index` succeeds |
| `test_creates_memory_index` | `FT.INFO memory_index` succeeds |
| `test_indexes_survive_second_ensure_call` | Calling `ensure_indexes()` again doesn't destroy data |

#### Dependencies

Phase 1 (Settings for `redis.url`, `redis.max_connections`, `embedding.dimension`, `redis.index_type`).

#### Done When

Integration tests pass against Docker Redis (`docker compose up -d`). All three indexes exist and are queryable via `FT.INFO`. Connection pool opens and closes cleanly.

---

### Phase 3: Ingestion Pipeline

**Goal**: A complete pipeline that reads files from disk, splits them into chunks with metadata, generates embeddings, and stores everything in Redis as hash keys under the `chunk:` prefix.

**Why third**: This is Layer 1 of the architecture. Without indexed chunks, there is nothing to search. The ingestion pipeline is the first "real" feature and the foundation of the MVP.

#### Files to Implement

| File | What it does |
|------|-------------|
| `src/contextflow/ingestion/loader.py` | `load_file(path) -> Document` -- reads a single file, returns `Document(text, filename, filepath)`. `load_directory(path) -> list[Document]` -- recursively loads all supported files. Validates against `settings.ingestion.supported_formats`. |
| `src/contextflow/ingestion/chunker.py` | `chunk_document(document, settings) -> list[Chunk]` -- recursive character splitting (split on `\n\n`, then `\n`, then ` `). Token counting via `tiktoken`. Section extraction from nearest markdown heading above the chunk. SHA-256 `doc_id` from file content (enables idempotent re-ingestion). |
| `src/contextflow/ingestion/embedder.py` | `Embedder` protocol with `embed_texts(texts) -> list[list[float]]` and `embed_text(text) -> list[float]`. `OpenAIEmbedder(settings)` implementation with batch support (`settings.embedding.batch_size`). |
| `src/contextflow/ingestion/pipeline.py` (new) | `ingest_pipeline(path, redis_client, embedder, settings) -> IngestResult` -- orchestrates: load -> chunk -> embed -> store to Redis (`HSET chunk:{doc_id}:{chunk_index}`). Returns count of chunks created. |
| `tests/fixtures/sample.md` (new) | Sample markdown file with headings, code blocks, and enough content to produce 3-5 chunks. Used by all ingestion and retrieval tests. |

#### Chunking Strategy Details

From the system design doc (Section 3.1):

- **Strategy**: Recursive character splitting with a 500-token target and 10% overlap (50 tokens)
- **Split hierarchy**: `\n\n` > `\n` > ` ` -- respects document structure
- **Token counting**: via `tiktoken` (the `cl100k_base` encoding used by OpenAI models)
- **Overlap**: Last ~50 tokens of chunk N appear at start of chunk N+1

**Why 500 tokens?** (from design doc):
- Below ~200: chunks are too narrow, embeddings lack self-contained meaning
- Above ~800: chunks are too broad, embeddings lose retrieval precision
- 500 is the empirical sweet spot for technical documentation

**Metadata schema** (from design doc Section 3.1):

```python
ChunkMetadata = {
    "doc_id":       str,   # SHA-256 hash of source file content
    "filename":     str,   # "redis-commands.md"
    "section":      str,   # Extracted from nearest heading above chunk
    "chunk_index":  int,   # Position within document (0-indexed)
    "token_count":  int,   # Actual token count of this chunk
    "char_offset":  int,   # Character offset in original document
    "version":      str,   # Document version tag if available
    "indexed_at":   int,   # Unix timestamp
}
```

**Why `doc_id` as content hash?** Enables idempotent re-ingestion. Same file content = same hash = skip. Changed content = new hash = re-index.

#### Embedding Model Details

From the system design doc (Section 3.2):

| Model | Dimension | Latency | Cost |
|-------|-----------|---------|------|
| `text-embedding-004` (default, Gemini) | 768 | ~50ms/call (batched) | Free (1,500 req/day) |
| `text-embedding-3-small` (OpenAI) | 1536 | ~50ms/call (batched) | $0.02/1M tokens |
| `all-MiniLM-L6-v2` (local) | 384 | ~5ms/chunk | Free |

**Critical constraint**: The same embedding model must be used at ingestion time and query time. Mismatch = vectors in different spaces = meaningless cosine distances. The embedding model name is stored alongside the index -- a config mismatch at query time must raise an error immediately.

#### Redis Storage Format

From the system design doc (Section 5):

```
Key pattern: chunk:{doc_id}:{chunk_index}
Type: Hash
Fields:
  text        -> chunk text content
  embedding   -> binary float32 vector (numpy.tobytes())
  filename    -> source filename
  section     -> section heading
  chunk_index -> position integer
  token_count -> token count integer
  version     -> version string
  indexed_at  -> unix timestamp integer
```

#### Tests to Write First

**`tests/unit/test_loader.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_loads_markdown_file` | Returns text content and filename metadata from a .md file |
| `test_loads_txt_file` | Same for .txt |
| `test_rejects_unsupported_format` | .csv raises `ValueError` with clear message |
| `test_loads_directory_recursively` | Given a directory, returns all supported files |
| `test_handles_empty_file` | Returns empty string, does not crash |
| `test_returns_document_with_correct_metadata` | `Document.filename` and `Document.filepath` are set |

**`tests/unit/test_chunker.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_chunks_short_text_into_single_chunk` | Text shorter than chunk_size returns one chunk |
| `test_chunks_long_text_into_multiple_chunks` | ~1500 token text returns ~3 chunks |
| `test_overlap_exists_between_adjacent_chunks` | Last ~50 tokens of chunk N appear at start of chunk N+1 |
| `test_chunk_metadata_has_required_fields` | Each chunk has doc_id, filename, section, chunk_index, token_count, char_offset |
| `test_doc_id_is_content_hash` | doc_id is SHA-256 of the source content |
| `test_section_extracted_from_nearest_heading` | For markdown, section reflects nearest heading above the chunk |
| `test_token_count_is_accurate` | Stored token_count matches tiktoken re-count |
| `test_chunk_index_is_sequential` | chunk_index goes 0, 1, 2, ... in order |
| `test_recursive_split_respects_hierarchy` | Splits on `\n\n` before `\n` before ` ` |

**`tests/unit/test_embedder.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_embed_single_text_returns_vector` | Returns `list[float]` of correct dimension |
| `test_embed_batch_returns_matching_count` | N texts in, N vectors out |
| `test_vector_dimension_matches_config` | Vector length equals `settings.embedding.dimension` |
| `test_empty_text_raises_error` | Empty string input raises `ValueError` |

(Use a mock for the OpenAI API call -- test the interface contract, not the API.)

**`tests/integration/test_ingestion_pipeline.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_ingest_file_stores_chunks_in_redis` | Ingest sample.md, verify `chunk:*` keys exist in Redis |
| `test_ingest_is_idempotent` | Ingest the same file twice, chunk count does not double |
| `test_chunks_searchable_after_ingestion` | `FT.SEARCH chunk_index "*"` returns results |
| `test_stored_metadata_matches_source` | Retrieved chunk metadata matches the original file |

#### Dependencies

Phase 1 (Settings, ChunkMetadata model), Phase 2 (Redis client, chunk_index).

#### Done When

Unit tests pass with mocked embedder. Integration test ingests `tests/fixtures/sample.md`, stores chunks in Redis, and confirms they are retrievable via `FT.SEARCH`.

---

### Phase 4: Retrieval Layer

**Goal**: Given a query vector, retrieve relevant chunks from Redis via vector search, BM25 text search, and RRF fusion. Optionally rerank with a cross-encoder.

**Why fourth**: With chunks indexed in Phase 3, retrieval is the next link in the query pipeline. The retrieval layer is independently testable against real indexed data, and its quality is the ceiling for the entire system.

> "A great LLM with bad retrieval gives confidently wrong answers. A mediocre LLM with great retrieval gives accurate, grounded answers." -- System Design Doc, Section 8

#### Files to Implement

| File | What it does |
|------|-------------|
| `src/contextflow/retrieval/vector_search.py` | `vector_search(client, query_vector, settings, filters?) -> list[SearchResult]` -- builds `FT.SEARCH chunk_index` command with KNN and optional pre-filters. Returns top-k chunks ranked by cosine similarity. |
| `src/contextflow/retrieval/text_search.py` | `text_search(client, query_text, settings) -> list[SearchResult]` -- `FT.SEARCH chunk_index` with BM25 on the `text` field. Catches exact term matches that vector search misses. |
| `src/contextflow/retrieval/hybrid.py` | `reciprocal_rank_fusion(ranked_lists, k=60) -> list[SearchResult]` -- pure function. `hybrid_search(client, query_vector, query_text, settings) -> list[SearchResult]` -- runs both searches concurrently (`asyncio.gather`), merges with RRF. |
| `src/contextflow/retrieval/reranker.py` | `rerank(query, chunks, settings) -> list[SearchResult]` -- optional cross-encoder scoring. When `settings.retrieval.use_reranker` is False, returns input unchanged. |

**New dataclass** (in `retrieval/__init__.py` or a shared types file):

```python
@dataclass
class SearchResult:
    chunk_id: str          # "chunk:{doc_id}:{chunk_index}"
    text: str              # chunk text content
    score: float           # similarity/fusion score
    metadata: ChunkMetadata
```

#### Hybrid Search and RRF Details

From the system design doc (Section 3.6):

**Why vector search alone is insufficient**: Semantic embeddings excel at conceptual similarity but can miss exact terminology. A query for `EXPIRE` might retrieve chunks about "key expiration" without surfacing the exact `EXPIRE` API docs. BM25 nails exact terms but has no semantic understanding.

**Reciprocal Rank Fusion**:

```
For each document d across all ranked lists:
    RRF_score(d) = sum( 1 / (k + rank(d, list_i)) )
    where k = 60 (dampening constant)

Sort by RRF_score descending.
```

**Why RRF over weighted combination?**
- Weighted combination requires both scores on the same scale -- they're not (BM25 is unbounded, cosine is [0,1])
- RRF uses only rank position, no calibration needed
- Empirically outperforms simple weighted fusion in IR benchmarks

**Pre-filter vs post-filter** (decision: pre-filter):

```python
# Pre-filter: search only within matching documents
FT.SEARCH chunk_index
    "(@version:[7.0 +inf] @doc_type:{commands})"
    => KNN 5 BY embedding $query_vector
```

#### Tests to Write First

**`tests/unit/test_vector_search.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_returns_chunks_ranked_by_similarity` | Results are ordered by cosine distance (ascending) |
| `test_respects_top_k_parameter` | Returns at most k results |
| `test_applies_similarity_threshold` | Chunks below threshold are excluded |
| `test_returns_empty_when_no_match` | Returns empty list, does not error |
| `test_applies_metadata_filters` | Pre-filters narrow the search space |

**`tests/unit/test_text_search.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_finds_exact_keyword_match` | Query "EXPIRE" finds chunks containing "EXPIRE" |
| `test_returns_empty_for_no_match` | Unrelated term returns empty list |
| `test_respects_top_k` | Returns at most k results |

**`tests/unit/test_hybrid.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_rrf_merges_two_ranked_lists` | Given two lists, RRF produces correct merged ranking |
| `test_rrf_handles_disjoint_lists` | Chunks in only one list still appear in output |
| `test_rrf_handles_empty_list` | One empty + one non-empty = non-empty results |
| `test_rrf_k_parameter_affects_scores` | Different k values produce different orderings for edge cases |
| `test_rrf_deduplicates` | Same chunk in both lists appears once with combined score |
| `test_rrf_score_calculation_is_correct` | For a known input, verify exact RRF scores |

**`tests/unit/test_reranker.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_reranker_reorders_by_score` | Output ordering differs from input |
| `test_reranker_respects_top_n` | Returns at most `rerank_top_n` |
| `test_reranker_disabled_returns_input` | When `use_reranker=False`, passthrough |

**`tests/integration/test_retrieval.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_vector_search_finds_ingested_chunk` | Ingest sample doc, query with similar vector, get the right chunk back |
| `test_text_search_finds_ingested_chunk` | Keyword query returns expected chunk |
| `test_hybrid_search_combines_results` | Hybrid finds chunks that only one method would find alone |
| `test_hybrid_runs_searches_concurrently` | Both searches execute via `asyncio.gather` |

#### Dependencies

Phase 2 (Redis client, indexes), Phase 3 (ingested chunks for integration tests).

#### Done When

RRF unit tests pass with purely in-memory data (no Redis). Integration tests prove that ingested chunks are retrievable by both vector similarity and keyword match, and that hybrid search combines them correctly.

---

### Phase 5: LLM Abstraction + Orchestrator (MVP Complete)

**Goal**: Abstract LLM interface with OpenAI and Gemini providers, prompt builder, and the query orchestrator. This phase produces the **Minimum Viable Pipeline**: ingest docs, ask a question, get a grounded answer with citations.

**Why fifth**: This is the keystone phase. It connects Phases 3 and 4 to LLMs and produces the first user-visible output. The orchestrator is a thin coordination layer -- the heavy logic lives in the components built in prior phases.

#### Files to Implement

| File | What it does |
|------|-------------|
| `src/contextflow/llm/base.py` | `LLMProvider` ABC with `async complete(messages, stream, max_tokens) -> str \| AsyncIterator[str]`. `Message` dataclass: `role: str`, `content: str`. |
| `src/contextflow/llm/openai.py` | `OpenAIProvider(settings)` -- uses the `openai` async client. Implements both streaming (yields tokens) and non-streaming (returns full string). |
| `src/contextflow/llm/gemini.py` | `GeminiProvider(settings)` -- uses Google Gemini API. Implements both streaming and non-streaming modes with proper message formatting. |
| `src/contextflow/llm/router.py` | `LLMRouter(settings)` -- holds primary + optional fallback provider. `async complete()` delegates to primary, catches exceptions, falls back to secondary if configured. |
| `src/contextflow/orchestrator.py` | `QueryOrchestrator` -- the 10-step pipeline. For MVP, steps 2-4 are stubbed (no cache, session, or long-term memory yet). |
| `prompts/rag_system_v1.txt` (new) | The RAG system prompt from `docs/04-Prompts/00-prompt-templates.md` Section 2. |

#### Orchestrator Pipeline (MVP Version)

From the system design doc (Section 3.5), simplified for MVP:

```
Step 1:  Embed query -> query_vector
Step 2:  [SKIP - cache lookup, Phase 7]
Step 3:  [SKIP - session history, Phase 6]
Step 4:  [SKIP - long-term facts, Phase 8]
Step 5:  Hybrid search (vector + BM25 + RRF)
Step 6:  [SKIP - reranker, optional]
Step 7:  Build prompt:
         - System: faithfulness constraints + citation format
         - Context: retrieved chunks with [source: id | location]
         - Query: user's question
Step 8:  Call LLM
Step 9:  Post-process:
         - Validate citations reference real chunk_ids
         - [SKIP - cache write, Phase 7]
         - [SKIP - session write, Phase 6]
Step 10: Return answer + citations + metadata
```

**Empty retrieval handling** (from design doc Section 6): If retrieval returns zero chunks above the similarity threshold, return "No relevant documentation found for this query" **without calling the LLM**. This prevents hallucination on queries outside the knowledge base.

#### Prompt Construction

From the prompt templates doc (`docs/04-Prompts/00-prompt-templates.md` Section 2):

**System prompt** instructs the LLM to:
1. Answer using ONLY the provided context
2. Refuse with an exact phrase when context is insufficient
3. Cite sources using `[source_id]` inline after every claim
4. Never use training knowledge

**Chunk injection format**:

```
[source: chunk_4821 | redis-commands.md section EXPIRE]
Use the EXPIRE command to set a timeout on a key...
```

Each chunk is wrapped with its `source_id` (used in citations) and human-readable location (filename + section).

**"Lost in the Middle" consideration** (from RAG deep-dive Section 2.2): Place the highest-relevance chunks at position 1 and position N (first and last), not buried in the middle.

#### Tests to Write First

**`tests/unit/test_llm_base.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_interface_requires_complete_method` | Subclass without `complete()` raises TypeError |
| `test_message_dataclass_fields` | `Message` has `role` and `content` fields |

**`tests/unit/test_llm_openai.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_formats_messages_correctly` | Messages formatted as OpenAI API expects |
| `test_returns_string_when_not_streaming` | Non-stream mode returns `str` |
| `test_returns_async_iterator_when_streaming` | Stream mode returns `AsyncIterator[str]` |
| `test_handles_api_error_with_clear_message` | API error re-raised with context (model, error type) |
| `test_respects_max_tokens` | `max_tokens` passed to API call |
| `test_respects_temperature` | `temperature` passed to API call |

**`tests/unit/test_llm_gemini.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_formats_messages_correctly` | Messages formatted as Gemini API expects |
| `test_returns_string_when_not_streaming` | Non-stream mode returns `str` |
| `test_returns_async_iterator_when_streaming` | Stream mode returns `AsyncIterator[str]` |
| `test_handles_api_error_with_clear_message` | API error re-raised with context (model, error type) |
| `test_respects_max_tokens` | `max_tokens` passed to API call |
| `test_respects_temperature` | `temperature` passed to API call |
| `test_handles_safety_filters` | Proper handling of Gemini safety filters and content policies |

**`tests/unit/test_llm_router.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_routes_to_configured_provider` | Router calls the correct provider based on settings |
| `test_fallback_on_primary_failure` | If primary raises, falls back to secondary |
| `test_raises_when_no_fallback_and_primary_fails` | Clear error when no fallback configured |
| `test_logs_fallback_event` | Falling back logs a WARNING with primary error details |

**`tests/unit/test_orchestrator.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_embed_query_called` | Orchestrator calls embedder with the query text |
| `test_retrieval_called_with_query_vector` | Retrieval receives the embedded query vector |
| `test_prompt_includes_retrieved_chunks` | Prompt sent to LLM contains chunk text with source IDs |
| `test_prompt_includes_system_instructions` | System prompt contains faithfulness constraints |
| `test_response_includes_citations` | Response has `Citation` objects with chunk_id, filename, section |
| `test_handles_no_retrieval_results` | Returns "No relevant documentation found" without LLM call |
| `test_citation_validation_strips_invalid` | Citations referencing non-retrieved chunks are stripped |

**`tests/integration/test_end_to_end_mvp.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_ingest_then_query_returns_grounded_answer_openai` | Ingest sample.md -> query about its content -> get an answer that references the content (real Redis, OpenAI LLM) |
| `test_ingest_then_query_returns_grounded_answer_gemini` | Ingest sample.md -> query about its content -> get an answer that references the content (real Redis, Gemini LLM) |
| `test_query_with_no_relevant_docs_refuses` | Query about unrelated topic returns refusal message |
| `test_provider_fallback_works` | Primary provider failure falls back to secondary provider |

#### Dependencies

Phases 1-4 (everything built so far). Add `google-generativeai` package for Gemini support.

#### Provider Configuration

Both OpenAI and Gemini providers will be supported:
- **OpenAI**: Requires `OPENAI_API_KEY` environment variable
- **Gemini**: Requires `GEMINI_API_KEY` environment variable
- **Router**: Configurable primary/fallback provider in settings

#### Done When

End-to-end integration test passes: ingest a sample document, submit a query, receive a grounded answer with citations using both OpenAI and Gemini providers. **This is the MVP milestone.** `pytest tests/` passes -- all unit tests (with mocked LLM/embedder) and integration tests (with real Redis, real LLMs) are green.

---

### Phase 6: Session Memory

**Goal**: Per-session conversation history stored in Redis Streams. Multi-turn follow-up questions work. Sessions have 24h TTL and MAXLEN ~100.

**Why sixth**: With the MVP working, session memory is the most impactful enhancement. It enables follow-up questions ("What about the TTL on that?") which are essential for a useful assistant.

**Parallel with**: Phases 7 and 8 (independent enhancements to the orchestrator).

#### Files to Implement

| File | What it does |
|------|-------------|
| `src/contextflow/memory/session.py` | `SessionMemory(client, settings)` with methods: `create_session() -> str` (generate unique session_id), `add_turn(session_id, role, content)` (XADD), `get_recent_turns(session_id, n) -> list[Turn]` (XRANGE + limit), `get_full_history(session_id) -> list[Turn]`, `delete_session(session_id)` (DEL). Uses MAXLEN ~100, sets TTL via EXPIRE. |
| Update `src/contextflow/orchestrator.py` | Integrate step 3 (retrieve session history, inject into prompt as `User: ... \n Assistant: ...`) and step 9 (append user turn and assistant turn after response). |

#### Redis Streams Details

From the system design doc (Section 3.7):

**Why Streams over Lists?** Streams give ordered, append-only history with native trimming (`MAXLEN`) and rich per-entry metadata. `XRANGE` reads in chronological order. `MAXLEN ~100` prevents unbounded growth. Lists lack per-entry metadata.

```
Key:          session:{session_id}
Entry fields: {role: "user"|"assistant", content: "...", timestamp: ...}
MAXLEN:       ~100 entries (auto-trim oldest)
TTL:          24h (set on key via EXPIRE)
```

**Context window management** (from design doc Section 3.7):
- Default: sliding window of last 10 turns (`settings.session.context_window_turns`)
- Token budget: 10 turns x ~200 tokens = ~2,000 tokens -- well within any model's context
- Summarization trigger: after 50 turns (deferred to Phase 10)

#### Tests to Write First

**`tests/unit/test_session.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_create_session_returns_unique_id` | Two creates produce different IDs |
| `test_add_turn_appends_to_stream` | After adding a turn, stream length increases by 1 |
| `test_get_recent_turns_returns_last_n` | Requesting last 5 from a 20-turn session returns exactly 5 |
| `test_turns_are_in_chronological_order` | Oldest first in returned list |
| `test_delete_session_removes_stream` | After delete, key does not exist |
| `test_maxlen_enforced` | Adding turns beyond MAXLEN trims the oldest |
| `test_turn_has_role_and_content` | Returned Turn objects have role, content, timestamp |

**`tests/integration/test_session.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_session_ttl_set` | After creating and adding turns, TTL is ~86400 seconds |
| `test_multi_turn_conversation_flows` | Add user turn -> add assistant turn -> retrieve history -> verify ordering and content |
| `test_session_survives_redis_persistence` | Data is in Redis Streams, not just in-memory |

#### Dependencies

Phase 2 (Redis client), Phase 5 (orchestrator to integrate into).

#### Done When

A multi-turn conversation works: query 1 gets an answer, query 2 with the same `session_id` can reference query 1's context. Integration test confirms Redis Stream persistence and TTL.

---

### Phase 7: Semantic Cache

**Goal**: Before calling the LLM, check if a semantically similar query was already answered. If cosine distance is below threshold (0.10), return the cached answer instantly. After LLM call, write the result to cache.

**Why seventh**: The cache is a performance optimization on top of the working pipeline. It does not change correctness -- it's a transparent bypass layer.

**Parallel with**: Phases 6 and 8.

#### Files to Implement

| File | What it does |
|------|-------------|
| `src/contextflow/cache/semantic_cache.py` | `SemanticCache(client, embedder, settings)` with methods: `lookup(query_vector) -> CacheEntry \| None` (FT.SEARCH cache_index KNN 1, check distance against threshold), `store(query_text, query_vector, answer, source_chunks, model_used)` (HSET + EXPIRE). |
| Update `src/contextflow/orchestrator.py` | Integrate step 2 (cache lookup before retrieval -- on hit, return immediately with `from_cache: True`) and step 9 (cache write after LLM -- only if answer has valid citations when `require_citations` is True). |

#### Cache Architecture Details

From the system design doc (Section 3.4):

```
Incoming Query -> Embed -> query_vector
                              |
                              v
                    FT.SEARCH cache_index
                      KNN 1 BY query_vector
                              |
                    +---------+---------+
                    |                   |
                distance < 0.10     distance >= 0.10
                    |                   |
                    v                   v
              Return cached        Continue to
              answer (HIT)         LLM pipeline (MISS)
```

**Threshold tuning** (from design doc):

| Threshold | Behavior | Hit Rate | Risk |
|-----------|----------|----------|------|
| 0.05 | Near-exact match only | Very low (~5%) | Safe but useless |
| 0.10 | Paraphrases match | Moderate (~15-25%) | Good balance |
| 0.15 | Semantically similar | High (~30-40%) | Some wrong answers |
| 0.20+ | Loosely related | Very high | Dangerous |

**Decision**: Start at 0.10 (conservative). Log every cache hit with its distance value for offline tuning.

**Cache poisoning mitigation** (from design doc Section 3.4):
1. Only cache answers with at least one valid citation (`require_citations: true`)
2. TTL on cache entries (7 days default)
3. Log every cache hit with distance for offline review

**Cache entry schema** (from design doc):

```
Key:    cache:{sha256_of_query_text}
Type:   Hash
Fields: query_text, query_vector (binary), answer, source_chunks,
        model_used, created_at, hit_count
TTL:    7 days
```

#### Tests to Write First

**`tests/unit/test_semantic_cache.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_cache_miss_returns_none` | Empty cache always returns None |
| `test_cache_hit_returns_stored_answer` | After storing, a similar query vector returns the entry |
| `test_distance_above_threshold_is_miss` | A distant query vector returns None |
| `test_hit_count_incremented_on_hit` | `hit_count` increases by 1 on each hit |
| `test_cache_write_stores_all_fields` | All fields present: query_text, answer, source_chunks, model_used, created_at |
| `test_cache_requires_citations_when_configured` | Answer without citations is not cached when `require_citations=True` |
| `test_cache_allows_no_citations_when_unconfigured` | Answer cached when `require_citations=False` |

**`tests/integration/test_semantic_cache.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_cache_roundtrip_in_redis` | Write to cache, look up, verify match |
| `test_ttl_set_on_cache_entry` | Entry has TTL matching `settings.cache.ttl_seconds` |
| `test_cache_lookup_via_vector_search` | FT.SEARCH on cache_index finds the right entry |
| `test_paraphrased_query_hits_cache` | "How to install Redis?" and "Redis installation steps" are a cache hit |
| `test_different_query_misses_cache` | "How to install Redis?" and "How to uninstall Redis?" are a cache miss |

#### Dependencies

Phase 2 (Redis client, cache_index), Phase 3 (embedder), Phase 5 (orchestrator).

#### Done When

Query the same question twice -- second time returns instantly from cache with `from_cache: True`. Paraphrased questions also hit cache. Different questions miss cache and go through the full pipeline.

---

### Phase 8: Long-Term Memory + Remaining LLM Providers

**Goal**: (A) Fact extraction from conversations, vector-indexed persistent facts, and injection into future queries. (B) Gemini and Ollama LLM providers.

**Why eighth**: Long-term memory is explicitly non-core (system design doc Section 6: "Long-term memory failure = log and skip"). It depends on sessions (Phase 6) existing to extract from, and on the LLM for extraction. The additional LLM providers are straightforward implementations of the abstract interface from Phase 5.

**Parallel with**: Phases 6 and 7.

#### Files to Implement

| File | What it does |
|------|-------------|
| `src/contextflow/memory/long_term.py` | `LongTermMemory(client, embedder, llm_router, settings)` with methods: `extract_facts(conversation) -> list[MemoryFact]` (LLM call with fact extraction prompt), `store_fact(user_id, fact)` (HSET memory:{user_id}:{fact_hash}), `retrieve_facts(user_id, query_vector) -> list[MemoryFact]` (KNN on memory_index, filter by similarity threshold), handle contradictions (replace, not append), handle reconfirmation (update `last_confirmed_at`). |
| `src/contextflow/llm/gemini.py` | `GeminiProvider(settings)` -- Gemini API implementation. |
| `src/contextflow/llm/ollama.py` | `OllamaProvider(settings)` -- uses OpenAI-compatible API at `base_url` (default: `http://localhost:11434/v1`). |
| `prompts/fact_extraction_v1.txt` (new) | Fact extraction prompt from `docs/04-Prompts/00-prompt-templates.md` Section 3. |
| Update `src/contextflow/orchestrator.py` | Integrate step 4 (retrieve relevant long-term facts, inject into prompt) and post-session fact extraction (async, non-blocking). |

#### Long-Term Memory Details

From the system design doc (Section 3.8):

**What gets stored** (examples):
- "User primarily works with Python"
- "User is debugging a Redis cluster in production"
- "User prefers concise answers over detailed explanations"

**Storage schema** (from design doc Section 5):

```
Key:    memory:{user_id}:{fact_hash}
Type:   Hash
Fields: fact_text, fact_vector (binary), confidence, last_confirmed_at, source_session
```

**Failure modes** (from design doc):
1. **Staleness**: Facts become irrelevant over time -> TTL of 30 days
2. **Contradiction**: "Codes in Python" then "Switched to Go" -> replace, not append
3. **Extraction noise**: Over-extraction -> confidence threshold (0.6 minimum)
4. **Relevance at injection**: Not all facts are relevant to every query -> vector similarity filter (`retrieval_threshold: 0.7`), inject at most `max_facts_injected: 5`

**Graceful degradation**: If fact extraction or retrieval fails, log a WARNING and continue. The query pipeline must function without long-term memory.

#### Tests to Write First

**`tests/unit/test_long_term_memory.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_extract_facts_returns_fact_list` | Given conversation text, returns list of MemoryFact |
| `test_filters_low_confidence_facts` | Facts below `min_confidence` are dropped |
| `test_store_fact_creates_redis_hash` | Fact stored with all fields |
| `test_retrieve_relevant_facts` | Given query vector, returns facts within threshold |
| `test_max_facts_injected_respected` | Returns at most `max_facts_injected` |
| `test_contradiction_replaces_old_fact` | New contradicting fact overwrites old |
| `test_reconfirmation_updates_timestamp` | Same fact re-extracted updates `last_confirmed_at` |
| `test_extraction_failure_does_not_crash` | LLM error during extraction logs and returns empty list |

**`tests/unit/test_llm_gemini.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_formats_messages_for_gemini_api` | Gemini-specific message formatting |
| `test_handles_gemini_api_error` | Error caught and re-raised with context |

**`tests/unit/test_llm_ollama.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_uses_openai_compatible_endpoint` | `base_url` is `http://localhost:11434/v1` |
| `test_handles_ollama_timeout` | Timeout produces clear error |
| `test_inherits_openai_provider_behavior` | Reuses OpenAI client with different base_url |

#### Dependencies

Phase 5 (LLM base, router), Phase 6 (session history to extract from), Phase 2 (Redis, memory_index).

#### Done When

After a session with identifiable user facts, those facts appear in the `memory_index`. A subsequent query retrieves and injects relevant facts into the prompt. Gemini and Ollama providers pass unit tests. Long-term memory failure does not crash the pipeline.

---

### Phase 9: API Layer + CLI

**Goal**: FastAPI application with all routes (query, ingest, session, metrics, health) and the Click CLI entrypoint. SSE streaming for query responses.

**Why ninth**: The API is a thin mapping layer over the components built in Phases 1-8. Every route handler delegates to an already-tested component. Building the API last means the business logic is solid and the API tests focus on HTTP contracts.

#### Files to Implement

| File | What it does |
|------|-------------|
| `src/contextflow/api/app.py` | FastAPI app with lifespan handler: connect Redis, create indexes, initialize orchestrator on startup; close Redis on shutdown. Mount all route modules. |
| `src/contextflow/api/routes/query.py` | `POST /query` -- calls orchestrator, returns `QueryResponse`. `GET /query/stream` -- SSE via `StreamingResponse` (text/event-stream). |
| `src/contextflow/api/routes/ingest.py` | `POST /ingest` -- calls `ingest_pipeline()`, returns `IngestResponse` with chunk count. |
| `src/contextflow/api/routes/session.py` | `POST /session` -- create session, return id. `DELETE /session/{id}` -- delete session. `GET /session/{id}/history` -- return conversation turns. |
| `src/contextflow/api/routes/metrics.py` | `GET /metrics` -- reads `metrics:global` hash, returns `MetricsResponse`. `GET /health` -- ping Redis, check indexes exist, return status. |
| `src/contextflow/main.py` | Click CLI with commands: `ingest` (path), `query` (question text), `serve` (starts uvicorn), `metrics` (print metrics), `cache-clear` (flush cache). |

#### API Endpoints

From the system design doc (Section 4):

```
POST   /ingest               Ingest a document or directory
POST   /query                Submit a query (returns full answer)
GET    /query/stream         SSE stream for real-time token output
POST   /session              Create a new session -> returns session_id
DELETE /session/{id}         Delete session history
GET    /session/{id}/history Return conversation history
GET    /metrics              System performance metrics
GET    /health               Liveness check
DELETE /cache                Flush semantic cache (admin)
GET    /chunks               List indexed chunks with metadata (debug)
```

**Request/Response contract** (from design doc Section 4):

```json
// POST /query
Request:
{
  "query": "How do I set TTL on a Redis key?",
  "session_id": "sess_abc123",     // optional
  "filters": { "filename": "..." },  // optional
  "stream": false
}

Response:
{
  "answer": "Use the EXPIRE command...",
  "citations": [
    { "chunk_id": "chunk_4821", "filename": "redis-commands.md", "section": "EXPIRE" }
  ],
  "from_cache": false,
  "latency_ms": 1240,
  "session_id": "sess_abc123"
}
```

**Streaming decision** (from design doc Section 3.9): Stream tokens to client via SSE. Buffer internally. Post-process asynchronously after stream completes (cache write, citation validation). The user gets fast UX; correctness checks happen in background.

#### Tests to Write First

**`tests/unit/test_api_routes.py`** (using `httpx.AsyncClient` / TestClient):

| Test | What it verifies |
|------|-----------------|
| `test_post_query_returns_200` | Valid query body returns 200 with `QueryResponse` |
| `test_post_query_returns_422_on_empty_query` | Empty query field returns 422 validation error |
| `test_post_ingest_returns_200` | Valid path returns 200 with chunk count |
| `test_post_session_returns_session_id` | Returns JSON with `session_id` field |
| `test_delete_session_returns_204` | Delete returns 204 No Content |
| `test_get_session_history_returns_turns` | Returns ordered list of turns |
| `test_get_metrics_returns_all_sections` | Response has cache, retrieval, llm, memory fields |
| `test_get_health_returns_status` | Returns `{ "status": "healthy", ... }` |
| `test_query_stream_returns_sse_content_type` | Content-Type is `text/event-stream` |
| `test_delete_cache_flushes_entries` | Cache entries removed after DELETE /cache |

**`tests/unit/test_cli.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_ingest_command_accepts_path` | `contextflow ingest /path/to/docs` invokes ingest pipeline |
| `test_query_command_accepts_question` | `contextflow query "How..."` runs the orchestrator |
| `test_serve_command_starts_server` | `contextflow serve` starts uvicorn |
| `test_cache_clear_command` | `contextflow cache-clear` flushes the cache |

#### Dependencies

All prior phases (the API delegates to every component).

#### Done When

`uvicorn contextflow.api.app:app` starts and serves requests. All API tests pass. The CLI can ingest a file, run a query, and display metrics from the terminal.

---

### Phase 10: Optimization, Metrics, and Evaluation Harness

**Goal**: History summarization for long sessions, Redis-based metrics tracking, graceful degradation hardening, and the evaluation harness.

**Why last**: These are refinements on a fully working system. Each optimization requires the full pipeline to be in place so its impact can be measured. The evaluation harness validates the entire stack against the eval set.

#### Files to Implement

| File | What it does |
|------|-------------|
| Update `src/contextflow/memory/session.py` | Add `summarize_history(session_id)` method. Uses the summarization prompt template. Triggered when turn count exceeds `settings.session.summarize_after_turns`. |
| `src/contextflow/metrics.py` (new) | `MetricsTracker(client)` with methods: `record_cache_hit()`, `record_cache_miss()`, `record_query_latency(ms)`, `record_llm_call(tokens, model)`, `record_ingestion(chunks, duration)`, `get_metrics() -> MetricsResponse`. Uses Redis `HINCRBY` on `metrics:global`. |
| Update `src/contextflow/orchestrator.py` | Wrap each non-core step (cache, session, long-term memory) in try/except with WARNING log and graceful skip. Add timing instrumentation (start/end timestamps per step). Integrate MetricsTracker calls. |
| `prompts/summarization_v1.txt` (new) | Summarization prompt from `docs/04-Prompts/00-prompt-templates.md` Section 4. |
| Populate `tests/eval/eval_set.json` | 30-50 evaluation questions per `docs/03-Evaluation/00-evaluation-strategy.md` Section 2. |
| `scripts/run_eval.py` (new) | Evaluation runner: reads eval_set.json, runs retrieval eval (Hit@K, MRR, Precision@K), generation eval (faithfulness via LLM judge), cache eval (hit rate, accuracy), end-to-end eval. Outputs results JSON to `tests/eval/results/`. |

#### History Summarization Details

From the prompt templates doc (Section 4):

- **Trigger**: when session exceeds `summarize_after_turns` (default: 50)
- **Output**: bullet points, under 500 tokens
- **Must preserve**: user's original question, key decisions, corrections, unresolved questions
- **Must discard**: greetings, repetitions, tangential resolved discussions

#### Metrics Schema

From the system design doc (Section 3.10):

```json
{
  "cache": { "total_queries": 1240, "cache_hits": 387, "hit_rate_percent": 31.2, "avg_cache_latency_ms": 12 },
  "retrieval": { "avg_retrieval_latency_ms": 23, "avg_chunks_returned": 4.2 },
  "llm": { "avg_generation_latency_ms": 1840, "total_tokens_used": 48200, "total_cost_usd": 0.024 },
  "memory": { "redis_used_memory_mb": 87, "total_chunks_indexed": 11420, "active_sessions": 3 }
}
```

Storage: Redis Hash at key `metrics:global` with atomic `HINCRBY` increments. No separate monitoring database needed.

#### Graceful Degradation Matrix

From the system design doc (Section 6):

| Component | Failure Mode | Detection | Degradation Strategy |
|-----------|-------------|-----------|---------------------|
| Redis connection | Unreachable | Ping + connection error | **Fail-fast** -- Redis is load-bearing |
| Semantic cache | Lookup fails | Exception | Log WARNING, skip cache, continue to LLM |
| Embedding service | Timeout / rate limit | HTTP 429, timeout | Retry with exponential backoff (max 3) |
| LLM API | Timeout, 500 | HTTP error | Retry once, then try fallback provider |
| Long-term memory | Extraction fails | Exception | Log and skip -- session still completes |
| Retrieval empty | No chunks above threshold | k=0 results | Return refusal message without LLM call |
| Citation validation | LLM cites wrong chunk | Post-processing check | Strip invalid citations, don't cache answer |

**Key principle**: Each layer must be independently bypassable. Only the chunk index and LLM are truly load-bearing.

#### Evaluation Harness Details

From the evaluation strategy doc (`docs/03-Evaluation/00-evaluation-strategy.md`):

**Eval set structure** (30-50 questions):

| Category | What it tests | Target count |
|----------|--------------|-------------|
| Single-hop | One chunk has the answer | 10-15 |
| Comparison | Answer requires 2+ chunks | 5-10 |
| Multi-hop | Answer spans multiple documents | 5-10 |
| Exact term | Specific command/API name | 5-8 |
| Paraphrase | Same question, different wording | 5-8 |
| Unanswerable | Answer NOT in the docs | 3-5 |

**Metrics targets**:

| Metric | Minimum | Good | Excellent |
|--------|---------|------|-----------|
| Hit@5 | 70% | 85% | 95%+ |
| MRR | 0.5 | 0.7 | 0.85+ |
| Faithfulness | 4.0/5 | 4.5/5 | -- |
| Citation accuracy | 90% | 98%+ | -- |
| Refusal on unanswerable | 80% | 95%+ | -- |

**The One Rule**: Never tune a layer above without confirming the layer below is working. Fix bottom-up: chunking -> retrieval -> generation -> cache -> end-to-end.

#### Tests to Write First

**`tests/unit/test_history_summarization.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_summarize_long_history` | 60-turn history compressed to ~500 tokens |
| `test_summarization_triggered_by_threshold` | Only triggered when turns > `summarize_after_turns` |
| `test_summary_preserves_corrections` | User corrections appear in summary |

**`tests/unit/test_metrics.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_increment_cache_hit` | Cache hit count increases by 1 |
| `test_increment_total_queries` | Total queries increases |
| `test_record_latency` | Latency recorded accurately |
| `test_get_metrics_returns_all_sections` | Metrics dict has cache, retrieval, llm, memory sections |

**`tests/unit/test_graceful_degradation.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_query_works_without_cache` | Cache down -> query still returns answer |
| `test_query_works_without_long_term_memory` | Memory failure -> query still works |
| `test_query_works_without_session` | Session failure -> query works (no history) |
| `test_query_fails_clearly_without_redis` | Redis unreachable -> clear error (not silent failure) |
| `test_llm_fallback_on_primary_failure` | Primary LLM down -> fallback provider used |

**`tests/eval/test_eval_runner.py`**:

| Test | What it verifies |
|------|-----------------|
| `test_eval_runner_processes_eval_set` | Reads eval_set.json, runs each question, produces results |
| `test_eval_computes_hit_at_k` | Correctly calculates Hit@5 |
| `test_eval_computes_mrr` | Correctly calculates Mean Reciprocal Rank |
| `test_eval_results_saved_to_json` | Results written to `tests/eval/results/` |

#### Dependencies

All prior phases.

#### Done When

The full pipeline handles component failures gracefully. Metrics endpoint returns accurate counts. The evaluation harness runs against the eval set and produces a results JSON in `tests/eval/results/`. History summarization compresses long sessions.

---

## 4. Dependency Graph

```
Phase 1 (Config + Types)
   |
   v
Phase 2 (Redis Client + Indexes)
   |
   v
Phase 3 (Ingestion Pipeline)
   |
   v
Phase 4 (Retrieval Layer)
   |
   v
Phase 5 (LLM + Orchestrator)  ---------->  MVP COMPLETE
   |
   +-------------+-------------+
   |             |             |
   v             v             v
Phase 6       Phase 7       Phase 8         (parallelizable)
(Session)     (Cache)       (LTM + LLMs)
   |             |             |
   +-------------+-------------+
                 |
                 v
            Phase 9 (API + CLI)
                 |
                 v
            Phase 10 (Optimization + Eval)
```

**Key sequencing rationale**:
- **Config first**: every module imports Settings -- no module should hardcode values
- **Ingestion before retrieval**: retrieval integration tests need real indexed data, not mocks
- **LLM with orchestrator**: the LLM interface is consumed only by the orchestrator -- build them together
- **Phases 6/7/8 parallel**: each is an independent enhancement to the orchestrator
- **API last**: it's a thin mapping layer -- test HTTP contracts after business logic is solid

---

## 5. Verification Strategy

Each phase is verified independently before moving to the next:

| Phase | Verification |
|-------|-------------|
| 1 | `pytest tests/unit/test_config.py tests/unit/test_models.py` all green |
| 2 | Integration tests pass against Docker Redis; `FT.INFO` confirms all three indexes |
| 3 | Sample markdown ingested; chunks visible in Redis via `FT.SEARCH chunk_index "*"` |
| 4 | Hybrid search returns correct chunks for test queries against real indexed data |
| 5 (**MVP**) | End-to-end: ingest doc -> query -> grounded answer with citations |
| 6 | Multi-turn conversation: query 2 references query 1's context via session_id |
| 7 | Same question twice -> second returns from cache with `from_cache: True` |
| 8 | User facts persist across sessions; Gemini/Ollama providers pass unit tests |
| 9 | `curl -X POST localhost:8000/query` returns valid response; CLI commands work |
| 10 | Eval harness produces results JSON; graceful degradation confirmed under component failure |

---

## 6. Token Budget Calculation

From the config reference (Section 4), the prompt token budget for each query:

```
System prompt:           ~500 tokens
Chunks:                  top_k(5) x chunk_size(500) = 2,500 tokens
Session history:         context_window_turns(10) x ~200 = 2,000 tokens
Long-term facts:         max_facts_injected(5) x ~30 = 150 tokens
User query:              ~50 tokens
Reserved for response:   max_tokens(1,024)
────────────────────────────────────────────────────
Total:                   ~6,224 tokens

GPT-4o-mini context:     128,000 tokens   <- plenty
Ollama llama3.1:8b:      8,192 tokens     <- tight (reduce history or top_k)
```

When using small local models, reduce `context_window_turns` to 3-5 and `top_k` to 3.

---

## Appendix A: Reference Documents

| Document | Location | What it covers |
|----------|----------|---------------|
| High-Level Blueprint | `docs/00-Overview/00-high-level-blueprint.md` | 7-layer stack, core requirements |
| CRISP-DM Mapping | `docs/internal/00-CRISP-DM-Plan.md` | How CRISP-DM phases map to this project |
| System Design | `docs/02-System-Design/00-system-design.md` | Full architecture, component deep-dives, data model, failure modes |
| RAG Deep Dive | `docs/02-System-Design/01-rag-deep-dive.md` | RAG concepts, failure modes, landscape of approaches |
| Evaluation Strategy | `docs/03-Evaluation/00-evaluation-strategy.md` | Layer-by-layer evaluation, metrics, eval set design |
| Prompt Templates | `docs/04-Prompts/00-prompt-templates.md` | All prompt templates with design rationale |
| Config Reference | `docs/05-Config/00-config-reference.md` | Every tunable parameter with defaults and ranges |
| Coding Standards | `CLAUDE.md` | TDD, code style, error handling, async, logging, testing, git conventions |

## Appendix B: New Files to Create

| File | Created in Phase |
|------|-----------------|
| `config.yaml` | Phase 1 |
| `src/contextflow/ingestion/pipeline.py` | Phase 3 |
| `tests/fixtures/sample.md` | Phase 3 |
| `prompts/rag_system_v1.txt` | Phase 5 |
| `prompts/fact_extraction_v1.txt` | Phase 8 |
| `prompts/summarization_v1.txt` | Phase 10 |
| `src/contextflow/metrics.py` | Phase 10 |
| `scripts/run_eval.py` | Phase 10 |

## Appendix C: Files Modified Across Multiple Phases

| File | Phases | What changes |
|------|--------|-------------|
| `src/contextflow/orchestrator.py` | 5, 6, 7, 8, 10 | Steps added incrementally: MVP pipeline -> +session -> +cache -> +long-term memory -> +graceful degradation + metrics |
| `tests/conftest.py` | 2, 3+ | Shared fixtures grow as new components are added |
| `src/contextflow/llm/router.py` | 5, 8 | OpenAI initially, then Gemini + Ollama providers registered |

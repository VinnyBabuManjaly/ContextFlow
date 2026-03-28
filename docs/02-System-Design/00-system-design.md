# ContextFlow — System Design Document

> **How to read this doc:** Each section is written in two voices.
> - *Interview lens* — how you'd explain a decision under time pressure to a system design interviewer.
> - *Actual lens* — the real nuance, edge cases, and reasons the clean answer is incomplete.

---

## 1. Requirements

### 1.1 Functional Requirements

**Core (P0):**
- Ingest technical documentation (Markdown, plain text, PDF) into a searchable knowledge base
- Answer natural-language queries grounded in the ingested documents, with citations
- Maintain per-session conversation history for multi-turn follow-up questions
- Serve cached responses for semantically similar queries without hitting the LLM

**Extended (P1):**
- Persist user-level facts across sessions (long-term personalization)
- Support hybrid search: vector similarity + keyword matching + metadata filters
- Expose observability metrics: cache hit rate, retrieval latency, estimated cost savings

**Out of scope (explicit):**
- Multi-user auth and isolation (single-user local system)
- Real-time document updates / live re-indexing
- Fine-tuning or training any models

---

### 1.2 Non-Functional Requirements

| Concern | Target | Reasoning |
|---|---|---|
| Query latency (cache hit) | < 100ms | Cache bypass of LLM must feel instant |
| Query latency (LLM path) | < 5s P95 | LLM call dominates; retrieval should add < 50ms |
| Ingestion throughput | ~1 MB/min | Local tooling, not a streaming pipeline |
| Availability | Best-effort (local) | Single-user; Redis restart is acceptable |
| Correctness | No hallucinated citations | System must cite chunks it actually retrieved |
| Graceful degradation | Core Q&A works if memory layers fail | Long-term memory and cache are non-essential |

---

### 1.3 Capacity Estimation

> **Interview note:** Always do back-of-envelope before drawing boxes. It tells you which components need to be serious and which can be simple.

**Assumptions (local developer tool):**
- Knowledge base: ~500 documents × ~20 pages × ~400 words/page ≈ **4M words ≈ 5M tokens**
- Chunk size: 500 tokens with 50-token overlap
- Chunks: 5M / 450 (net) ≈ **~11,000 chunks**
- Embedding dimension: 1536 (OpenAI text-embedding-3-small) or 768 (local model)
- Vector size per chunk: 1536 × 4 bytes (float32) = **6KB per chunk**
- Total vector storage: 11,000 × 6KB ≈ **66MB**
- Metadata per chunk (filename, section, position, tokens): ~200 bytes → **2.2MB**
- **Total Redis memory: ~100MB** — comfortably fits in RAM

**Query volume:**
- Developer tool: ~200 queries/day, bursts of 20 queries/hour
- Cache hit rate target: 30%+ (reduces LLM calls by ~60 per day)

**Conclusion:** This is a single-node, in-memory problem. No sharding. No replication. Redis Stack on a single container is the right call and not over-engineering.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          User / CLI                              │
└─────────────────────────┬───────────────────────────────────────┘
                          │ HTTP / WebSocket
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                        API Layer (FastAPI)                        │
│                                                                   │
│   POST /query          GET /metrics         POST /ingest          │
│   POST /session/new    GET /health          DELETE /session/{id}  │
└─────────┬───────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Query Orchestrator                            │
│  1. Embed query                                                   │
│  2. Check semantic cache  ──── HIT ──► return cached answer      │
│  3. Retrieve session memory                              │        │
│  4. Retrieve long-term facts                             │        │
│  5. Hybrid search (vector + BM25 + metadata filters)    │        │
│  6. Rerank results                                       │        │
│  7. Build prompt                                         │        │
│  8. Call LLM                                             │        │
│  9. Write to cache + session memory                      │        │
│ 10. Return answer + citations                            │        │
└─────────┬───────────────────────────────────────────────┘        │
          │                                                         │
          ▼                                                         │
┌─────────────────────┐     ┌──────────────────────────────────────┘
│   Redis Stack        │     │   LLM Router
│                      │     │
│  ┌───────────────┐  │     │   ┌─────────────┐  ┌───────────────┐
│  │ Vector Index  │  │     │   │ OpenAI API  │  │  Gemini API   │
│  │ (HNSW)        │  │     │   └─────────────┘  └───────────────┘
│  └───────────────┘  │     │   ┌─────────────┐
│  ┌───────────────┐  │     │   │ Ollama      │
│  │ Semantic Cache│  │     │   │ (local)     │
│  └───────────────┘  │     │   └─────────────┘
│  ┌───────────────┐  │
│  │ Session Memory│  │
│  └───────────────┘  │
│  ┌───────────────┐  │
│  │ Long-Term Mem │  │
│  └───────────────┘  │
└─────────────────────┘
          ▲
          │
┌─────────────────────┐
│  Ingestion Pipeline  │
│                      │
│  Load → Clean →      │
│  Chunk → Embed →     │
│  Store               │
└─────────────────────┘
```

---

## 3. Component Deep-Dives

### 3.1 Ingestion Pipeline

**What it does:** Takes raw documents, converts them into indexed, searchable vector chunks stored in Redis.

#### Chunking Strategy Decision

> **Interview answer:** Fixed-size chunking at 500 tokens with 50-token overlap. Simple, predictable, fast.
>
> **Actual answer:** Fixed-size is the baseline. The right strategy depends on document structure.

| Strategy | Pros | Cons | Use when |
|---|---|---|---|
| Fixed-size (token) | Uniform, predictable, fast | Cuts mid-sentence/mid-concept | Baseline; unstructured plain text |
| Recursive character | Respects `\n\n` > `\n` > ` ` hierarchy | Still semantically blind | General docs; good default |
| Structure-aware (headings) | Each chunk = one section | Sections vary wildly in length | Markdown/HTML with consistent headers |
| Semantic chunking | Split where embedding distance spikes | Expensive (requires embedding pass), complex | High-quality corpora; offline indexing |

**Decision for ContextFlow:** Recursive character splitting with a 500-token target and 10% overlap (50 tokens).

**Why 500 tokens?**
- Below ~200 tokens: chunks are too narrow. A chunk like "See the section above for details" embeds terribly — it has no self-contained meaning.
- Above ~800 tokens: chunks are too broad. The embedding averages over too many concepts and loses retrieval precision.
- 500 tokens is the empirical sweet spot for technical documentation. Most API docs sections, code examples, and explanations fit within this range.

**Why overlap?**
Without overlap, a concept that spans a chunk boundary gets split. The first chunk gets context but no conclusion; the second gets conclusion but no context. Overlap ensures both sides contain the seam. 50 tokens ≈ 2-3 sentences — enough to preserve boundary context without duplicating significant content.

**Metadata schema:**

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

> **Why `doc_id` as content hash?** Enables idempotent re-ingestion. If you re-ingest a file that hasn't changed, the hash matches and you skip it. If the file changed, the hash is new and you re-index. No manual version tracking needed.

---

### 3.2 Embedding Model

**Decision:** Support both local (`sentence-transformers/all-MiniLM-L6-v2`, 384-dim) and cloud (`text-embedding-3-small`, 1536-dim) through a common interface.

> **Interview lens:** Pick OpenAI embeddings. They're state-of-the-art and fast to ship.
>
> **Actual lens:** The critical constraint is that **the same model must be used at ingestion time and query time**. If you index with `text-embedding-3-small` and query with `text-embedding-3-large`, you're comparing vectors in different spaces. The cosine distance will be meaningless. This is one of the most common production bugs in RAG systems.

| Model | Dimension | Latency | Cost | Quality |
|---|---|---|---|---|
| `all-MiniLM-L6-v2` (local) | 384 | ~5ms/chunk | Free | Good for English technical text |
| `all-mpnet-base-v2` (local) | 768 | ~10ms/chunk | Free | Better, heavier |
| `text-embedding-3-small` | 1536 | ~50ms/call (batched) | $0.02/1M tokens | Very good |
| `text-embedding-3-large` | 3072 | ~80ms/call | $0.13/1M tokens | Best, overkill for local |

**Decision:** Default to `text-embedding-3-small` with the option to swap. The embedding model name is stored in Redis alongside the index — a config mismatch at query time raises an error immediately rather than silently returning bad results.

---

### 3.3 Vector Index (Redis)

Redis Stack uses **HNSW** (Hierarchical Navigable Small World graphs) or **FLAT** (brute-force exact search).

| Index Type | How it works | Latency | Accuracy | Use when |
|---|---|---|---|---|
| FLAT | Compare query vector against every stored vector | O(n) | Exact (100%) | n < ~10K vectors |
| HNSW | Graph-based approximate nearest neighbor | O(log n) | ~95-99% ANN | n > 10K or latency is critical |

**Decision: FLAT first, HNSW ready.**

At 11,000 chunks with 1536-dim float32 vectors, a FLAT search takes ~2-5ms — well within budget. HNSW adds implementation complexity (tuning `M`, `ef_construction`, `ef_runtime` parameters) for marginal latency gain at this scale.

**However:** the Redis index schema should be designed to support switching. HNSW config parameters:
- `M=16` — number of connections per node. Higher = better recall, more memory.
- `ef_construction=200` — beam width during index build. Higher = better quality, slower ingestion.
- `ef_runtime=10` — beam width at query time. Tune recall vs. latency.

> **Interview trade-off question: "Why not just use HNSW from the start?"**
>
> FLAT is exact and eliminates one variable during development. When you're debugging why retrieval quality is poor, you want to know it's a chunking/embedding problem, not an ANN approximation problem. Switch to HNSW when the index grows beyond ~50K vectors or latency becomes a concern.

**Similarity metric: Cosine distance.**

- **Cosine** measures the angle between vectors — invariant to magnitude. "redis installation" and "how to install redis on ubuntu" will have similar angles even though their word counts differ.
- **L2 (Euclidean)** measures absolute distance — sensitive to vector magnitude. Less appropriate for text.
- **Dot product** is only equivalent to cosine when vectors are unit-normalized. Fine if your embeddings are normalized (most modern embedding models do this by default), but cosine is explicit and safe.

---

### 3.4 Semantic Cache

**The idea:** before calling the LLM, embed the incoming query and check if a semantically similar query was already answered. If the cosine distance to a cached query is below threshold `θ`, return the cached answer.

**Architecture:**

```
Incoming Query
      │
      ▼ Embed
  query_vector
      │
      ▼ FT.SEARCH cache_index
         KNN 1 BY embedding
         FILTER distance < θ
      │
  ┌───┴────────┐
  │ HIT        │ MISS
  ▼            ▼
Return       Continue
cached       to LLM
answer       pipeline
             │
             ▼
         Store in cache:
         {query_vector, answer, source_chunks, timestamp}
```

**Threshold tuning — this is a dial, not a constant:**

| Threshold | Behavior |
|---|---|
| 0.05 | Near-exact match only. Very few cache hits. Safe. |
| 0.10 | Paraphrases hit the cache. ~15-25% hit rate. |
| 0.15 | Semantically similar questions hit. ~30-40% hit rate. Some risk of wrong answers. |
| 0.20+ | Loosely related questions hit. High hit rate, high error rate. Dangerous. |

**Decision: Start at 0.10. Expose as a config variable. Log every cache hit with the distance for offline tuning.**

> **Actual concern: cache poisoning.**
> If a bad LLM answer gets cached (e.g., the LLM hallucinated), that bad answer gets served to similar queries indefinitely. Mitigations:
> 1. Only cache answers that include at least one valid citation
> 2. Add a `confidence_score` field; only cache above a threshold
> 3. TTL on cache entries (7 days default) — prevents stale answers from living forever

**What to store per cache entry:**

```python
CacheEntry = {
    "query_text":     str,           # Original query text (for debugging)
    "query_vector":   list[float],   # For KNN search
    "answer":         str,           # The LLM-generated answer
    "source_chunks":  list[str],     # chunk_ids used
    "model_used":     str,           # Which LLM generated this
    "created_at":     int,           # Unix timestamp
    "hit_count":      int,           # How many times this cache entry was served
}
```

---

### 3.5 Retrieval Pipeline (Full Query Path)

This is the most important flow in the system. Each step is a decision.

```
Query: "How do I configure TTL for Redis keys?"

Step 1: Query Preprocessing
   └─ Optionally rewrite query for better retrieval
      - Naive: use query as-is
      - HyDE: ask LLM to generate a hypothetical answer,
              embed that instead (improves recall for vague queries)
      - Decision: naive first; HyDE as optional enhancement

Step 2: Embed query → query_vector (1536-dim)

Step 3: Semantic cache lookup
   └─ KNN(1) search in cache index
   └─ If distance < 0.10: return cached answer (done)

Step 4: Parallel retrieval
   ├─ Vector search: KNN(k=5) in chunk index by query_vector
   ├─ Full-text search: BM25 on chunk text for "TTL Redis keys"
   └─ Merge: Reciprocal Rank Fusion

Step 5: Reranking (optional)
   └─ Cross-encoder model scores each chunk vs query
   └─ Re-sort, keep top 3

Step 6: Context assembly
   ├─ Retrieve session history (last N turns)
   └─ Retrieve long-term user facts

Step 7: Prompt construction
   └─ System: "Answer using only the provided context. Cite sources."
   └─ Context: [chunk_1] [chunk_2] [chunk_3]
   └─ History: [turn_1] [turn_2] ...
   └─ Query: "How do I configure TTL for Redis keys?"

Step 8: LLM call (with streaming)

Step 9: Post-processing
   ├─ Validate citations reference real chunk_ids
   ├─ Write answer to semantic cache
   └─ Append turn to session memory
```

---

### 3.6 Hybrid Search and Result Fusion

**Why vector search alone is insufficient:**

Semantic embeddings excel at conceptual similarity but can miss exact terminology. A query for `EXPIRE` command might retrieve semantically-related chunks about "key expiration" without surfacing the exact `EXPIRE` API docs. Full-text (BM25) nails exact terms but has no semantic understanding.

**Reciprocal Rank Fusion (RRF):**

```
Given two ranked lists (vector results, BM25 results):

For each document d:
    RRF_score(d) = Σ 1 / (k + rank(d, list_i))
    where k = 60 (constant, dampens extreme ranks)

Sort by RRF_score descending.
```

**Why RRF over weighted combination?**
- Weighted combination (`0.7 * cosine + 0.3 * bm25`) requires both scores to be on the same scale — they're not. BM25 scores range is unbounded; cosine distance is [0, 1].
- RRF only requires rank position, not raw score. No calibration needed. Works out of the box.
- Empirically shown to outperform simple weighted fusion in most IR benchmarks.

**Metadata filters as a pre-filter or post-filter:**

```python
# Pre-filter: only search within matching documents
# More efficient, fewer vectors searched
FT.SEARCH chunk_index
    "(@version:[7.0 +inf] @doc_type:{commands})"
    => KNN 5 BY embedding $query_vector

# Post-filter: search all, then filter results
# More flexible but wastes retrieval budget on filtered-out results
```

**Decision: Pre-filter.** At our scale, pre-filtering is faster and doesn't sacrifice recall because the filter space is still large enough to find relevant chunks.

---

### 3.7 Session Memory

**Data structure choice:**

| Redis type | Access pattern | Trade-off |
|---|---|---|
| List (`LPUSH`/`LRANGE`) | Append + read last N | Simple, O(1) append, O(N) range read. No metadata per message. |
| Hash per turn | Structured turn data | Full control, but awkward to read as ordered sequence. |
| Stream (`XADD`/`XRANGE`) | Ordered, timestamped entries | Best for ordered history with metadata. Native trim with `MAXLEN`. |

**Decision: Redis Streams.**

```
Key: session:{session_id}
Entry fields: {role: "user"/"assistant", content: "...", timestamp: ...}
MAXLEN: 100 entries (auto-trim oldest)
TTL: 24h (set on key, not entries)
```

Streams give you ordered, append-only history with native trimming and rich per-entry metadata. The `XRANGE` command reads in chronological order. `MAXLEN ~100` prevents unbounded growth.

**Context window management:**

At k=5 retrieved chunks (~500 tokens each = 2,500 tokens context) + system prompt (~500 tokens) + last 10 turns (~2,000 tokens) = ~5,000 tokens. GPT-4 context window is 128K tokens — this is fine.

But for a 100-turn session, history alone is 20,000 tokens. At that point:
- **Sliding window:** always include last N turns. Oldest context is lost.
- **Summarization:** after every M turns, ask the LLM to summarize the conversation so far. Store the summary, discard raw turns. This is a lossy compression.
- **Decision:** sliding window of last 10 turns as default. Summarization as a threshold-triggered optimization (if session > 50 turns).

---

### 3.8 Long-Term Memory

> **Actual lens:** This is the most complex, most failure-prone, and most interesting layer.

**What it does:**
After each session, an extraction step analyzes the conversation and identifies persistent user facts. These are stored as key-value pairs in Redis with vector embeddings for retrieval.

```
Facts stored might look like:
  - "User primarily works with Python"
  - "User is debugging a Redis cluster in production"
  - "User prefers concise answers over detailed explanations"
  - "User has asked about HNSW 3 times — likely building a vector search system"
```

**Extraction:** done by a secondary LLM call with a specialized prompt: *"Given this conversation, extract any facts about the user that would be useful to remember for future sessions. Return as a JSON array of {fact, confidence}."*

**Retrieval:** at query time, embed the incoming query and retrieve top-k relevant facts by cosine similarity. Inject them into the system prompt as context.

**Failure modes:**

1. **Staleness:** "User is debugging Redis in production" stored 6 months ago is likely irrelevant. Need decay/TTL.
2. **Contradiction:** "User codes in Python" stored at T=0, "User switched to Go" stored at T=6mo. Which is true? Need update strategy, not just append.
3. **Extraction noise:** LLMs over-extract. "How do I install Redis" should not create a fact "User doesn't know how to install Redis."
4. **Relevance at injection:** injecting all stored facts into every query is wasteful and noisy. Inject only the facts whose vector is within distance threshold of the query.

**Decision:** Store facts with a `last_confirmed_at` timestamp and `confidence` float. TTL of 30 days. If the same fact is re-confirmed in a new session, update `last_confirmed_at` and increase confidence. If the fact contradicts an existing one, replace (not append).

---

### 3.9 LLM Router

**Requirement:** LLM-agnostic. Support OpenAI, Gemini, Ollama.

**Interface:**

```python
class LLMRouter:
    async def complete(
        self,
        messages: list[Message],
        stream: bool = False,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str] | str: ...
```

All LLM providers implement this interface. Config specifies which provider + model to use. The orchestrator calls `router.complete()` and never knows which LLM is behind it.

**Streaming decision:**

> **Interview answer:** Use streaming. It improves perceived latency — user sees tokens as they arrive rather than waiting for full generation.
>
> **Actual answer:** Streaming complicates the post-processing step. You need the full answer to validate citations and write to the cache. Solutions:
> 1. Stream to UI, buffer internally, run post-processing after stream completes
> 2. Two passes: stream the answer, then async post-process (cache write, citation validation happens ~1s after UI receives full response)
>
> **Decision:** Stream tokens to the client. Buffer internally. Post-process asynchronously after stream completes. The user gets fast UX; correctness checks happen in background.

---

### 3.10 Observability

**Endpoint: `GET /metrics`** — returns JSON (or Prometheus format).

```json
{
  "cache": {
    "total_queries": 1240,
    "cache_hits": 387,
    "hit_rate_percent": 31.2,
    "avg_cache_latency_ms": 12,
    "estimated_llm_calls_saved": 387,
    "estimated_cost_saved_usd": 0.19
  },
  "retrieval": {
    "avg_retrieval_latency_ms": 23,
    "avg_chunks_returned": 4.2,
    "avg_rerank_latency_ms": 45
  },
  "llm": {
    "avg_generation_latency_ms": 1840,
    "total_tokens_used": 48200,
    "total_cost_usd": 0.024
  },
  "memory": {
    "redis_used_memory_mb": 87,
    "total_chunks_indexed": 11420,
    "active_sessions": 3
  }
}
```

**What to instrument:**
- Every cache lookup: hit/miss + distance value
- Every retrieval: top-k scores, latency
- Every LLM call: token count, latency, model used
- Every ingestion: chunks created, time taken

**Where to store metrics:** Redis Hashes with atomic `HINCRBY` increments. No separate monitoring database needed.

---

## 4. API Design

### Endpoints

```
POST   /ingest               Ingest a document or directory
POST   /query                Submit a query (returns full answer)
GET    /query/stream         SSE stream for real-time token output
POST   /session              Create a new session → returns session_id
DELETE /session/{id}         Delete session history
GET    /session/{id}/history Return conversation history
GET    /metrics              System performance metrics
GET    /health               Liveness check (Redis connected, models loaded)
DELETE /cache                Flush semantic cache (admin)
GET    /chunks               List indexed chunks with metadata (debug)
```

### Request/Response contract

```json
// POST /query
{
  "query": "How do I set TTL on a Redis key?",
  "session_id": "sess_abc123",   // optional; creates new if omitted
  "filters": {                   // optional metadata filters
    "filename": "redis-commands.md",
    "version_min": "7.0"
  },
  "stream": false
}

// Response
{
  "answer": "Use the EXPIRE command: EXPIRE key seconds ...",
  "citations": [
    { "chunk_id": "chunk_4821", "filename": "redis-commands.md", "section": "EXPIRE" },
    { "chunk_id": "chunk_4822", "filename": "redis-commands.md", "section": "EXPIREAT" }
  ],
  "from_cache": false,
  "latency_ms": 1240,
  "session_id": "sess_abc123"
}
```

---

## 5. Redis Data Model

```
# Chunk index (vector search + metadata)
Key pattern: chunk:{doc_id}:{chunk_index}
Type: Hash
Fields: text, embedding (binary), filename, section, chunk_index, token_count, version, indexed_at

# Vector index (over chunk hashes)
FT.CREATE chunk_index ON HASH PREFIX 1 chunk:
  SCHEMA
    text           TEXT        (BM25 full-text)
    embedding      VECTOR HNSW 6 TYPE FLOAT32 DIM 1536 DISTANCE_METRIC COSINE
    filename       TAG
    section        TEXT
    version        NUMERIC

# Semantic cache
Key pattern: cache:{sha256_of_query_text}
Type: Hash
Fields: query_text, query_vector (binary), answer, source_chunks, model_used, created_at, hit_count
TTL: 7 days

# Cache vector index
FT.CREATE cache_index ON HASH PREFIX 1 cache:
  SCHEMA
    query_vector   VECTOR FLAT 6 TYPE FLOAT32 DIM 1536 DISTANCE_METRIC COSINE

# Session memory
Key pattern: session:{session_id}
Type: Stream
Entry fields: role, content, timestamp
MAXLEN: ~100
TTL: 24h

# Long-term memory
Key pattern: memory:{user_id}:{fact_hash}
Type: Hash
Fields: fact_text, fact_vector (binary), confidence, last_confirmed_at, source_session

# Memory vector index
FT.CREATE memory_index ON HASH PREFIX 1 memory:
  SCHEMA
    fact_vector    VECTOR FLAT 6 TYPE FLOAT32 DIM 1536 DISTANCE_METRIC COSINE

# Metrics
Key: metrics:global
Type: Hash
Fields: total_queries, cache_hits, total_llm_calls, total_tokens, ...
```

---

## 6. Failure Modes and Graceful Degradation

> **This section is what separates a junior design from a senior one.** Most system designs focus on the happy path. Real systems fail.

| Component | Failure Mode | Detection | Degradation Strategy |
|---|---|---|---|
| Redis connection | Redis unreachable | Ping on startup; catch connection errors | Fail-fast on startup. At runtime: return error with clear message. |
| Semantic cache | Cache lookup fails | Exception in cache lookup | Log warning, skip cache, continue to LLM. Cache failure is non-fatal. |
| Embedding service | API timeout / rate limit | HTTP 429, timeout exception | Retry with exponential backoff (max 3 retries). If still failing, return error. |
| LLM API | Timeout, 500 error | HTTP error codes | Retry once. If primary LLM fails, optionally fall back to secondary (e.g., Ollama local). |
| Long-term memory | Memory extraction fails | Exception in extraction step | Log and skip. Session still completes without personalization. |
| Retrieval returns empty | No chunks above similarity threshold | k=0 results | Return "No relevant documentation found for this query" without LLM call. |
| Citation validation fails | LLM cites chunk_id that wasn't retrieved | Post-processing check | Strip invalid citations, flag in response metadata, do not cache this answer. |

**Key principle:** Each layer must be independently bypassable. The query pipeline should function if long-term memory is down, if cache is down, even if session history is unavailable. Only the chunk index and LLM are truly load-bearing.

---

## 7. Key Design Decisions Summary

| Decision | Choice | Rejected Alternative | Why |
|---|---|---|---|
| Vector DB | Redis Stack | Pinecone, Weaviate, Chroma | Redis is the "brain" — session memory, cache, and vectors all in one system. No polyglot persistence overhead. |
| Vector index type | FLAT (initially) | HNSW | Exact search at 11K vectors is fast enough. HNSW adds tuning complexity for marginal gain. |
| Similarity metric | Cosine | L2 / Dot product | Cosine is magnitude-invariant. Safe default for NLP embeddings. |
| Chunk size | 500 tokens + 50 overlap | 256, 1024 | Empirical sweet spot for technical documentation. Overlap prevents boundary loss. |
| Result fusion | RRF | Weighted linear combination | RRF is score-scale-agnostic. No calibration needed. |
| Cache threshold | 0.10 (tunable) | Fixed 0.15 | Exposed as config. Start conservative; tune up based on hit rate vs. accuracy data. |
| Session storage | Redis Streams | Redis Lists | Streams give ordered, metadata-rich entries with native MAXLEN trimming. |
| LLM interface | Abstract router | Direct SDK calls | Swap LLMs without touching orchestration logic. |
| Streaming | SSE + async post-process | Blocking full response | Better perceived UX; post-processing on buffer after stream completes. |
| Metrics storage | Redis Hash (HINCRBY) | Prometheus, external TSDB | Zero extra infrastructure. Atomic increments. Sufficient for local tool. |

---

## 8. What This Project Teaches You (The Real Takeaways)

These are the things you can only learn by building:

1. **Retrieval is the ceiling; generation is the floor.** A great LLM with bad retrieval gives confidently wrong answers. A mediocre LLM with great retrieval gives accurate, grounded answers. Invest in retrieval quality first.

2. **The chunk boundary problem is real.** A critical piece of information that lives on a chunk boundary will be systematically missed. Overlap is not optional — it's correctness insurance.

3. **Evaluate retrieval separately from generation.** Build `{question, expected_chunk_ids}` test cases. Measure Hit@K. If your retrieval Hit@5 is below 70%, no LLM tuning will save you.

4. **The semantic cache threshold is a product decision, not a config detail.** Too tight: no performance gain. Too loose: users see wrong cached answers and lose trust in the system. This threshold determines the UX contract.

5. **Metadata schema is retrieval leverage.** Every field you store at ingestion time is a filter you can use at query time. Cheap to add during ingestion; expensive to add after (requires re-indexing).

6. **LLM faithfulness requires prompt engineering, not luck.** Without explicit "answer ONLY using the provided context" instructions and structured citation format in the prompt, LLMs will hallucinate with full confidence. Prompting is architecture.

7. **Long-term memory is RAG applied to conversation history.** The pattern is identical: embed, store, retrieve by similarity, inject into prompt. Once you understand RAG retrieval, long-term memory is a natural extension.

8. **Graceful degradation is architecture, not error handling.** You don't add graceful degradation after the system is built. You design for it from the start by treating every non-core component as optional.

9. **Redis is surprisingly capable as a full RAG backend.** Vector search, full-text search, sorted sets, streams, hashes, TTL — it covers everything in this stack without needing a separate vector database, message queue, or cache store.

10. **The hardest problem is knowing when the system is wrong.** A RAG system that says "I don't know" when it can't find relevant chunks is better than one that answers confidently from stale cache or bad retrieval. Explicit confidence signaling is a feature, not a fallback.

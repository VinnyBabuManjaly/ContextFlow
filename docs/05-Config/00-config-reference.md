# Configuration Reference

> Single source of truth for every tunable parameter in the system. Each entry describes what it controls, its default, valid range, and what happens when you change it.

---

## 1. Config File Location

```
config.yaml         ← primary config (checked into repo with safe defaults)
.env                 ← secrets only (never checked in)
```

**Precedence:** environment variable > `.env` file > `config.yaml` defaults.

---

## 2. Full Parameter Reference

### 2.1 Ingestion

| Parameter | Default | Range | What it controls |
|---|---|---|---|
| `ingestion.chunk_size` | `500` | 100-1500 tokens | Target token count per chunk |
| `ingestion.chunk_overlap` | `50` | 0-200 tokens | Token overlap between adjacent chunks |
| `ingestion.chunking_strategy` | `"recursive"` | `"recursive"`, `"fixed"`, `"heading"` | How documents are split into chunks |
| `ingestion.supported_formats` | `["md", "txt", "pdf"]` | list of extensions | File types the loader accepts |

**Tuning notes:**

- `chunk_size`: Start at 500. If retrieval eval shows low precision (too many irrelevant chunks), decrease to 300. If chunks lack context (incomplete answers), increase to 700. Always re-run retrieval eval after changing.
- `chunk_overlap`: 10% of chunk_size is the baseline. Set to 0 only to test the impact — you'll see chunk boundary failures immediately.
- `chunking_strategy`: `"recursive"` is the default. Switch to `"heading"` for well-structured Markdown with consistent headers. `"fixed"` exists for benchmarking only.

---

### 2.2 Embedding

| Parameter | Default | Range | What it controls |
|---|---|---|---|
| `embedding.provider` | `"gemini"` | `"gemini"`, `"openai"`, `"local"` | Which embedding model to use |
| `embedding.model` | `"text-embedding-004"` | any valid model name | Specific model identifier |
| `embedding.dimension` | `768` | depends on model | Vector dimension (must match model output) |
| `embedding.batch_size` | `100` | 1-2048 | Chunks per embedding API call during ingestion |

**Tuning notes:**

- `provider`: `"gemini"` is the default (free tier: 1,500 requests/day). `"openai"` gives slightly higher quality embeddings but costs $0.02/1M tokens. `"local"` (sentence-transformers) is free but lower dimension (384) and slightly lower quality.
- `batch_size`: Higher = fewer API calls during ingestion = faster. Limited by API max. Set to 100 for a balance of speed and manageable error retries.
- **Critical:** `dimension` must match the model. `text-embedding-004` = 768. `text-embedding-3-small` = 1536. `all-MiniLM-L6-v2` = 384. Mismatch = silent failure (Redis stores wrong-sized vectors, search returns garbage).

---

### 2.3 Retrieval

| Parameter | Default | Range | What it controls |
|---|---|---|---|
| `retrieval.top_k` | `5` | 1-20 | Number of chunks to retrieve per search method |
| `retrieval.similarity_threshold` | `0.7` | 0.0-1.0 | Minimum cosine similarity to include a chunk (1.0 = exact match) |
| `retrieval.vector_weight` | `0.7` | 0.0-1.0 | Weight for vector search in hybrid fusion (if using weighted, not RRF) |
| `retrieval.fusion_method` | `"rrf"` | `"rrf"`, `"weighted"` | How vector and BM25 results are merged |
| `retrieval.rrf_k` | `60` | 1-100 | RRF dampening constant (higher = more uniform weighting across ranks) |
| `retrieval.use_reranker` | `false` | `true`, `false` | Whether to apply cross-encoder reranking after fusion |
| `retrieval.rerank_top_n` | `3` | 1-10 | How many chunks to keep after reranking |

**Tuning notes:**

- `top_k`: Start at 5. Measure Precision@K. If most retrieved chunks are irrelevant, reduce to 3. If relevant chunks are consistently at position 6-7, increase to 8.
- `similarity_threshold`: A safety floor — chunks below this score are excluded even if they're in the top-k. Set at 0.7 initially. If too many queries return 0 results, lower to 0.5. If results include too much noise, raise to 0.8.
- `fusion_method`: `"rrf"` is the default and recommended. Only switch to `"weighted"` if you have empirical evidence that specific weights outperform RRF for your corpus.
- `rrf_k`: 60 is the standard value from the original paper. Lower values (10-30) amplify top-ranked results. Higher values (60-100) flatten the distribution. Rarely needs changing.
- `use_reranker`: Adds ~50-200ms latency per query. Only enable after baseline retrieval eval confirms that the right chunks are being retrieved but ranked wrong.

---

### 2.4 Semantic Cache

| Parameter | Default | Range | What it controls |
|---|---|---|---|
| `cache.enabled` | `true` | `true`, `false` | Master switch for semantic cache |
| `cache.distance_threshold` | `0.10` | 0.01-0.30 | Maximum cosine distance for a cache hit (lower = stricter) |
| `cache.ttl_seconds` | `604800` | 0-2592000 | Cache entry expiration (default: 7 days) |
| `cache.max_entries` | `10000` | 100-100000 | Maximum cached queries (oldest evicted first) |
| `cache.require_citations` | `true` | `true`, `false` | Only cache answers that contain at least one valid citation |

**Tuning notes:**

- `distance_threshold`: **The most sensitive parameter in the system.** Start at 0.10 (conservative). Log every cache hit with its distance value. Review hits in the 0.08-0.12 range manually. If accuracy is high, cautiously increase to 0.12-0.15 for better hit rate. If you see wrong cached answers, decrease immediately.
- `ttl_seconds`: 7 days is good for stable documentation. If your docs change frequently, reduce to 1-3 days. Set to 0 to disable TTL (cache lives until evicted or manually cleared).
- `require_citations`: Keep `true`. This prevents caching hallucinated answers that lack any citation — the cheapest quality gate.

---

### 2.5 Session Memory

| Parameter | Default | Range | What it controls |
|---|---|---|---|
| `session.max_turns` | `100` | 10-1000 | Maximum conversation turns stored per session (Redis MAXLEN) |
| `session.context_window_turns` | `10` | 1-50 | How many recent turns to include in the LLM prompt |
| `session.ttl_seconds` | `86400` | 3600-604800 | Session expiration (default: 24 hours) |
| `session.summarize_after_turns` | `50` | 20-200 | Trigger history summarization after this many turns |

**Tuning notes:**

- `context_window_turns`: 10 turns ≈ 2000 tokens of history. Safe for 128K-context models. If you're using a small local model with 4K-8K context, reduce to 3-5.
- `summarize_after_turns`: Summarization is a lossy compression step — it discards detail. Only trigger it when history starts competing with chunk context for token budget. At 50 turns with ~200 tokens each = 10K tokens, which is a good trigger point.

---

### 2.6 Long-Term Memory

| Parameter | Default | Range | What it controls |
|---|---|---|---|
| `long_term_memory.enabled` | `true` | `true`, `false` | Master switch for fact extraction and persistence |
| `long_term_memory.ttl_days` | `30` | 7-365 | Fact expiration if not re-confirmed |
| `long_term_memory.min_confidence` | `0.6` | 0.0-1.0 | Minimum confidence score to store an extracted fact |
| `long_term_memory.max_facts_injected` | `5` | 1-20 | Maximum facts to inject into a query prompt |
| `long_term_memory.retrieval_threshold` | `0.7` | 0.0-1.0 | Minimum similarity to inject a fact (only relevant facts) |

**Tuning notes:**

- `min_confidence`: Below 0.5, you'll store noise ("User might be interested in databases" from a single Redis question). Above 0.8, you'll only store explicit statements, missing useful inferences.
- `max_facts_injected`: Each fact is ~20-50 tokens. 5 facts = ~100-250 tokens. Keep low to avoid crowding out chunk context. Facts are supplementary, not primary context.

---

### 2.7 LLM

| Parameter | Default | Range | What it controls |
|---|---|---|---|
| `llm.provider` | `"gemini"` | `"gemini"`, `"openai"`, `"ollama"` | Primary LLM provider |
| `llm.model` | `"gemini-2.0-flash"` | any valid model name | Specific model to use |
| `llm.base_url` | `null` | URL string | Override API base URL (used for Ollama: `http://localhost:11434/v1`) |
| `llm.max_tokens` | `1024` | 100-4096 | Maximum tokens in LLM response |
| `llm.temperature` | `0.1` | 0.0-2.0 | Randomness in LLM output |
| `llm.stream` | `true` | `true`, `false` | Stream tokens to client |
| `llm.timeout_seconds` | `30` | 5-120 | Maximum wait time for LLM response |
| `llm.fallback.provider` | `null` | same as `llm.provider` | Fallback LLM if primary fails |
| `llm.fallback.model` | `null` | any valid model name | Fallback model |

**Tuning notes:**

- `temperature`: 0.1 for documentation Q&A. You want deterministic, grounded answers, not creative ones. 0.0 is technically possible but some providers behave slightly better at 0.1.
- `max_tokens`: 1024 is enough for most answers. If answers are getting truncated (cut off mid-sentence), increase. If answers are too verbose, decrease and add "Be concise" to the prompt.
- `timeout_seconds`: 30s is generous for cloud APIs. Ollama on CPU with a 7B model can take 15-30s for long answers. Increase to 60s if using local models.

---

### 2.8 Redis

| Parameter | Default | Range | What it controls |
|---|---|---|---|
| `redis.url` | `"redis://localhost:6379"` | Redis URL | Connection string |
| `redis.max_connections` | `10` | 5-50 | Connection pool size |
| `redis.index_type` | `"FLAT"` | `"FLAT"`, `"HNSW"` | Vector index algorithm |
| `redis.hnsw_m` | `16` | 4-64 | HNSW: connections per node (if index_type = HNSW) |
| `redis.hnsw_ef_construction` | `200` | 50-500 | HNSW: beam width during build (if index_type = HNSW) |
| `redis.hnsw_ef_runtime` | `10` | 10-500 | HNSW: beam width during search (if index_type = HNSW) |

**Tuning notes:**

- `index_type`: Stay on `"FLAT"` until you have 50K+ chunks. FLAT is exact search — eliminates one variable during debugging.
- `max_connections`: 10 is fine for single-user. Only increase if you see "connection pool exhausted" errors under concurrent load (unlikely for personal use).
- HNSW parameters: only relevant when `index_type = "HNSW"`. Higher `M` = better recall, more memory. Higher `ef_runtime` = better recall, slower queries. Start with defaults, tune only with retrieval eval data.

---

### 2.9 API Server

| Parameter | Default | Range | What it controls |
|---|---|---|---|
| `server.host` | `"127.0.0.1"` | IP address | Bind address (localhost only for security) |
| `server.port` | `8000` | 1024-65535 | HTTP port |
| `server.workers` | `1` | 1-4 | Uvicorn worker processes |

---

### 2.10 Observability

| Parameter | Default | Range | What it controls |
|---|---|---|---|
| `logging.level` | `"INFO"` | `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"` | Minimum log level |
| `logging.format` | `"json"` | `"json"`, `"text"` | Log output format |
| `metrics.enabled` | `true` | `true`, `false` | Track cache hit rates, latency, token usage |

---

## 3. Example config.yaml

```yaml
ingestion:
  chunk_size: 500
  chunk_overlap: 50
  chunking_strategy: "recursive"
  supported_formats: ["md", "txt", "pdf"]

embedding:
  provider: "gemini"
  model: "text-embedding-004"
  dimension: 768
  batch_size: 100

retrieval:
  top_k: 5
  similarity_threshold: 0.7
  fusion_method: "rrf"
  rrf_k: 60
  use_reranker: false
  rerank_top_n: 3

cache:
  enabled: true
  distance_threshold: 0.10
  ttl_seconds: 604800
  max_entries: 10000
  require_citations: true

session:
  max_turns: 100
  context_window_turns: 10
  ttl_seconds: 86400
  summarize_after_turns: 50

long_term_memory:
  enabled: true
  ttl_days: 30
  min_confidence: 0.6
  max_facts_injected: 5
  retrieval_threshold: 0.7

llm:
  provider: "gemini"
  model: "gemini-2.0-flash"
  max_tokens: 1024
  temperature: 0.1
  stream: true
  timeout_seconds: 30
  fallback:
    provider: "ollama"
    model: "llama3.1:8b"

redis:
  url: "redis://localhost:6379"
  max_connections: 10
  index_type: "FLAT"

server:
  host: "127.0.0.1"
  port: 8000
  workers: 1

logging:
  level: "INFO"
  format: "json"

metrics:
  enabled: true
```

---

## 4. Parameter Interaction Map

Some parameters affect each other. Changing one may require adjusting another.

```
chunk_size ──────────────► embedding quality ──────► retrieval quality
     │                                                      │
     └──► token count per chunk ──► context window budget ◄─┘
                                          │
              session.context_window_turns ┘
                     │
                     ▼
         Total prompt tokens = system_prompt (~500)
                             + chunks (top_k × chunk_size)
                             + history (context_window_turns × ~200)
                             + user query (~50)
                             + facts (max_facts_injected × ~30)

         Must fit within: model context window - llm.max_tokens
```

**Example calculation:**

```
System prompt:     500 tokens
Chunks:            5 × 500 = 2,500 tokens
History:           10 × 200 = 2,000 tokens
User query:        50 tokens
Facts:             5 × 30 = 150 tokens
Reserved for response: 1,024 tokens
─────────────────────────────────
Total:             6,224 tokens

GPT-4o-mini context: 128,000 tokens  ← plenty of room
Ollama llama3.1:8b:  8,192 tokens    ← tight! Reduce history or top_k
```

When using a small local model, you may need to reduce `context_window_turns` to 3-5 and `top_k` to 3 to fit within the context window.

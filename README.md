# ContextFlow

A full-stack AI assistant powered by Retrieval-Augmented Generation (RAG). Ingest technical documentation, ask natural-language questions, and get grounded answers with citations. Redis Stack serves as the unified backend for vector search, semantic caching, session memory, and long-term personalization.

> **Status:** In active development. Architecture and design are complete; implementation is underway.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
  - [1. Clone the Repository](#1-clone-the-repository)
  - [2. Start Redis](#2-start-redis)
  - [3. Set Up Environment](#3-set-up-environment)
  - [4. Install Dependencies](#4-install-dependencies)
- [Usage](#usage)
  - [CLI](#cli)
  - [API](#api)
- [Configuration](#configuration)
- [Project Structure](#project-structure)
- [Development](#development)
  - [Running Tests](#running-tests)
  - [Code Quality](#code-quality)
  - [TDD Workflow](#tdd-workflow)
- [API Reference](#api-reference)
- [How It Works](#how-it-works)
- [Documentation](#documentation)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## Features

- **Document Ingestion** -- Ingest Markdown, plain text, and PDF files into a searchable vector knowledge base
- **Grounded Answers with Citations** -- Every answer is derived from the ingested documents with inline source references
- **Hybrid Search** -- Combines vector similarity (cosine) with BM25 keyword matching via Reciprocal Rank Fusion
- **Semantic Cache** -- Serves instant responses for semantically similar queries without calling the LLM
- **Multi-Turn Conversations** -- Session memory via Redis Streams enables follow-up questions with full context
- **Long-Term Personalization** -- Extracts and persists user facts across sessions for tailored responses
- **LLM-Agnostic** -- Supports OpenAI, Google Gemini, and local models via Ollama with automatic fallback
- **Observability** -- Built-in metrics for cache hit rates, retrieval latency, token usage, and cost savings
- **Graceful Degradation** -- Non-essential components (cache, memory) can fail without breaking core Q&A

---

## Architecture

```
User / CLI
     |
     v
API Layer (FastAPI)
     |
     v
Query Orchestrator
  1. Embed query
  2. Check semantic cache ---- HIT --> return cached answer
  3. Retrieve session history            |
  4. Retrieve long-term facts            |
  5. Hybrid search (vector + BM25)       |
  6. Rerank results                      |
  7. Build prompt                        |
  8. Call LLM                            |
  9. Cache answer + update session       |
 10. Return answer + citations           |
     |                                   |
     v                                   |
Redis Stack                    LLM Router
  - Vector Index (FLAT/HNSW)     - OpenAI
  - Semantic Cache               - Gemini
  - Session Memory               - Ollama (local)
  - Long-Term Memory
     ^
     |
Ingestion Pipeline
  Load -> Chunk -> Embed -> Store
```

Redis Stack is the single backend -- no separate vector database, cache store, or message queue required.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.11+ |
| API Framework | FastAPI + Uvicorn |
| Database | Redis Stack (vector search, caching, streams, hashes) |
| Embeddings | OpenAI `text-embedding-3-small` (1536-dim) or local `sentence-transformers` (384-dim) |
| LLM Providers | OpenAI, Google Gemini, Ollama |
| Tokenization | tiktoken |
| CLI | Click |
| Validation | Pydantic v2 |
| Config | YAML + environment variables |
| Testing | pytest, pytest-asyncio |
| Linting | Ruff |
| Type Checking | mypy (strict mode) |
| Containerization | Docker Compose |

---

## Prerequisites

- **Python 3.11+**
- **Docker** and **Docker Compose** (for Redis Stack)
- **An LLM API key** (at least one of the following):
  - `OPENAI_API_KEY` -- for OpenAI embeddings and LLM
  - `GOOGLE_API_KEY` -- for Google Gemini
  - [Ollama](https://ollama.ai) installed locally -- for free, offline operation

---

## Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/your-username/ContextFlow.git
cd ContextFlow
```

### 2. Start Redis

```bash
docker compose up -d
```

This starts Redis Stack on port `6379` with RedisInsight available at `http://localhost:8001` for debugging.

### 3. Set Up Environment

```bash
cp .env.example .env
```

Edit `.env` and add your API key(s):

```env
OPENAI_API_KEY=sk-...
# GOOGLE_API_KEY=...        # optional, for Gemini
# REDIS_URL=redis://localhost:6379  # default, change if needed
```

### 4. Install Dependencies

```bash
# Production + dev tools
pip install -e ".[dev]"

# Or use Make (also starts Redis)
make dev
```

For local embedding models (free, offline):

```bash
pip install -e ".[local]"
```

---

## Usage

### CLI

```bash
# Ingest documentation
contextflow ingest /path/to/docs

# Ask a question
contextflow query "How do I set a TTL on a Redis key?"

# Start the API server
contextflow serve

# View system metrics
contextflow metrics

# Clear the semantic cache
contextflow cache-clear
```

### API

Start the server:

```bash
contextflow serve
# or
uvicorn contextflow.api.app:app --host 127.0.0.1 --port 8000
```

Query the API:

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How do I set a TTL on a Redis key?",
    "session_id": "sess_abc123"
  }'
```

Response:

```json
{
  "answer": "Use the EXPIRE command: EXPIRE key seconds ...",
  "citations": [
    {
      "chunk_id": "chunk_4821",
      "filename": "redis-commands.md",
      "section": "EXPIRE"
    }
  ],
  "from_cache": false,
  "latency_ms": 1240,
  "session_id": "sess_abc123"
}
```

---

## Configuration

ContextFlow uses a layered configuration system:

```
Environment variable  >  .env file  >  config.yaml defaults
```

### Key Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ingestion.chunk_size` | `500` | Target tokens per chunk (100-1500) |
| `ingestion.chunk_overlap` | `50` | Token overlap between adjacent chunks |
| `embedding.provider` | `"openai"` | `"openai"` or `"local"` (sentence-transformers) |
| `embedding.model` | `"text-embedding-3-small"` | Embedding model name |
| `retrieval.top_k` | `5` | Number of chunks to retrieve per query |
| `retrieval.fusion_method` | `"rrf"` | `"rrf"` (Reciprocal Rank Fusion) or `"weighted"` |
| `cache.distance_threshold` | `0.10` | Max cosine distance for a cache hit (lower = stricter) |
| `cache.ttl_seconds` | `604800` | Cache entry expiration (7 days) |
| `session.context_window_turns` | `10` | Recent conversation turns included in prompt |
| `session.ttl_seconds` | `86400` | Session expiration (24 hours) |
| `llm.provider` | `"openai"` | `"openai"`, `"gemini"`, or `"ollama"` |
| `llm.model` | `"gpt-4o-mini"` | LLM model name |
| `llm.temperature` | `0.1` | Low temperature for deterministic, grounded answers |
| `redis.index_type` | `"FLAT"` | `"FLAT"` (exact) or `"HNSW"` (approximate) |

See [`docs/05-Config/00-config-reference.md`](docs/05-Config/00-config-reference.md) for the complete parameter reference with tuning notes.

---

## Project Structure

```
ContextFlow/
├── src/contextflow/
│   ├── main.py              # CLI entrypoint
│   ├── config.py            # Configuration loader
│   ├── orchestrator.py      # 10-step query pipeline
│   ├── ingestion/           # Document loading, chunking, embedding
│   ├── retrieval/           # Vector search, BM25, hybrid fusion, reranking
│   ├── cache/               # Semantic cache (pre-LLM similarity check)
│   ├── memory/              # Session memory + long-term fact storage
│   ├── llm/                 # LLM abstraction (OpenAI, Gemini, Ollama)
│   ├── api/                 # FastAPI routes and Pydantic models
│   └── redis/               # Async client and index schemas
├── tests/
│   ├── unit/                # Logic tests (no external dependencies)
│   ├── integration/         # Tests against real Redis
│   └── eval/                # Retrieval and generation evaluation harness
├── docs/                    # Design documents and references
├── prompts/                 # Versioned prompt templates
├── scripts/                 # Utility scripts (evaluation runner, etc.)
├── config.yaml              # Default configuration
├── docker-compose.yml       # Redis Stack container
├── pyproject.toml           # Python project metadata and dependencies
├── Makefile                 # Common development commands
└── .env.example             # Environment variable template
```

---

## Development

### Running Tests

```bash
# All tests
make test

# Unit tests only (no external dependencies)
make test-unit

# Integration tests (requires Redis running)
make test-integration

# With verbose output
pytest tests/ -v
```

### Code Quality

```bash
# Lint
make lint

# Auto-format
make format

# Type check (strict mode)
make typecheck
```

### TDD Workflow

This project follows strict Test-Driven Development:

1. **Red** -- Write a failing test that defines the behavior you want
2. **Green** -- Write the minimum code to make it pass
3. **Refactor** -- Clean up without changing behavior

```bash
# Typical cycle
pytest tests/unit/test_chunker.py -v   # Run specific test file
ruff check src/                         # Lint
mypy src/                               # Type check
```

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/ingest` | Ingest a document or directory |
| `POST` | `/query` | Submit a query, get a full answer with citations |
| `GET` | `/query/stream` | SSE stream for real-time token output |
| `POST` | `/session` | Create a new conversation session |
| `DELETE` | `/session/{id}` | Delete a session and its history |
| `GET` | `/session/{id}/history` | Retrieve conversation history |
| `GET` | `/metrics` | Cache hit rates, latency, token usage, cost savings |
| `GET` | `/health` | Liveness check (Redis, indexes, LLM reachability) |
| `DELETE` | `/cache` | Flush the semantic cache |

---

## How It Works

### Ingestion

Documents are loaded, split into ~500-token chunks with 50-token overlap using recursive character splitting, converted to vectors via the configured embedding model, and stored in Redis as hash keys with full metadata (filename, section, position, token count).

### Query Pipeline

1. The query is embedded into a vector
2. **Semantic cache** is checked -- if a similar query was answered before (cosine distance < 0.10), the cached answer is returned instantly
3. **Session history** (last 10 turns) and **long-term user facts** are retrieved for context
4. **Hybrid search** runs vector KNN and BM25 keyword search in parallel, merging results via Reciprocal Rank Fusion
5. A **prompt** is constructed with system instructions, retrieved chunks (with source IDs), conversation history, and the user's question
6. The **LLM** generates an answer constrained to the provided context, with inline citations
7. Citations are validated, the answer is cached, and the conversation turn is saved

### Why Redis for Everything?

Redis Stack provides vector search, full-text search, streams, hashes, sorted sets, and TTL -- covering every storage need in this system. Using a single backend eliminates the operational overhead of managing separate vector databases, cache stores, and message queues. At ~100MB total memory for a typical knowledge base, it runs comfortably on a laptop.

---

## Documentation

| Document | Description |
|----------|-------------|
| [`docs/00-Overview/00-high-level-blueprint.md`](docs/00-Overview/00-high-level-blueprint.md) | 7-layer architectural stack and core requirements |
| [`docs/02-System-Design/00-system-design.md`](docs/02-System-Design/00-system-design.md) | Full system design with component deep-dives and trade-offs |
| [`docs/02-System-Design/01-rag-deep-dive.md`](docs/02-System-Design/01-rag-deep-dive.md) | RAG concepts, failure modes, and landscape of approaches |
| [`docs/03-Evaluation/00-evaluation-strategy.md`](docs/03-Evaluation/00-evaluation-strategy.md) | Layer-by-layer evaluation metrics and methodology |
| [`docs/04-Prompts/00-prompt-templates.md`](docs/04-Prompts/00-prompt-templates.md) | All prompt templates with design rationale |
| [`docs/05-Config/00-config-reference.md`](docs/05-Config/00-config-reference.md) | Complete parameter reference with tuning notes |
| [`docs/06-Implementation-Plan/00-implementation-plan.md`](docs/06-Implementation-Plan/00-implementation-plan.md) | 10-phase implementation plan with TDD test specifications |

---

## Roadmap

- [x] Architecture and system design
- [x] Module scaffolding and project setup
- [x] Implementation plan
- [ ] **Phase 1** -- Configuration and data types
- [ ] **Phase 2** -- Redis client and index schemas
- [ ] **Phase 3** -- Ingestion pipeline (loader, chunker, embedder)
- [ ] **Phase 4** -- Retrieval layer (vector, BM25, hybrid, reranker)
- [ ] **Phase 5** -- LLM abstraction and orchestrator (MVP)
- [ ] **Phase 6** -- Session memory (multi-turn conversations)
- [ ] **Phase 7** -- Semantic cache
- [ ] **Phase 8** -- Long-term memory and additional LLM providers
- [ ] **Phase 9** -- API layer and CLI
- [ ] **Phase 10** -- Optimization, metrics, and evaluation harness

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/your-feature`)
3. Write failing tests first (TDD)
4. Implement the minimum code to pass
5. Run the full test suite (`make test`)
6. Run linting and type checks (`make lint && make typecheck`)
7. Commit with a descriptive message (`feat: add cosine threshold config`)
8. Open a pull request

### Commit Convention

```
type: short description

Types: feat, fix, refactor, test, docs, chore
```

---

## License

This project is for educational and personal use. See the repository for license details.

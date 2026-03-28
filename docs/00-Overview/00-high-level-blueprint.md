## Project Blueprint: RTFM For Me Agent

### Core Objective

Build a full-stack AI assistant using **Retrieval-Augmented Generation (RAG)**. The system must ingest technical docs, answer queries with citations, and maintain a "brain" (Redis) for caching, short-term session memory, and long-term personalization.

### The 7-Layer Architectural Stack

| Layer | Component | Functional Requirement |
| --- | --- | --- |
| **0. Infrastructure** | Docker & Redis Stack | Orchestrate the environment; use Redis for Vector Search, Caching, and Storage. |
| **1. Ingestion** | The Pipeline | **Chunking:** Split docs (500 tokens + overlap). **Embedding:** Convert text to vectors. **Storage:** Save to Redis with metadata (filename, section, position). |
| **2. Retrieval (RAG)** | The Researcher | Convert query to vector → Search Redis → Fetch top $k$ chunks → Prompt LLM to answer **only** using provided context + citations. |
| **3. Performance** | Semantic Cache | Before LLM call, check if a *similar* query exists in Redis (Cosine Distance threshold $\approx 0.15$). If yes, serve instantly. |
| **4. Short-Term** | Session Memory | Store conversation history in Redis lists/streams by `session_id`. Include history in prompts for follow-up context. Set 24h TTL. |
| **5. Long-Term** | Agent Memory | Use **Redis Agent Memory Server** to extract user facts (e.g., "User codes in Go") and persist them across sessions for personalization. |
| **6. Optimization** | Hybrid & Hardening | **Hybrid Search:** Combine vector similarity with metadata filters. **Summarization:** Condense long chat histories. **Graceful Degradation:** Ensure system works if non-essential nodes fail. |


### Key Technical Constraints

* **Database:** Must use **Redis Stack** (Vector search capabilities are mandatory).
* **Search Logic:** Supports similarity search (vectors), full-text search, and metadata filtering.
* **LLM Integration:** Agnostic (Gemini, OpenAI, or local via Ollama).
* **Observability:** Expose a `/metrics` endpoint to track cache hit rates, latency, and estimated cost savings.


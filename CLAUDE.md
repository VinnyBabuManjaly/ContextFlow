# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**ContextFlow** is a full-stack AI assistant using Retrieval-Augmented Generation (RAG). It ingests technical documentation, answers queries with citations, and uses Redis as its "brain" for vector search, semantic caching, session memory, and long-term personalization.

> Note: This project is in active development. Phase 1 (Configuration and Data Types) is in progress. Default provider is Gemini (free tier).

## Architecture: 7-Layer Stack

| Layer | Component | Purpose |
|-------|-----------|---------|
| 0 | Docker & Redis Stack | Infrastructure; Redis handles vector search, caching, and storage |
| 1 | Ingestion Pipeline | Chunk docs (500 tokens + overlap) → embed → store in Redis with metadata (filename, section, position) |
| 2 | RAG Retrieval | Query → vector → Redis semantic search → top-k chunks → LLM prompt with citations |
| 3 | Semantic Cache | Pre-LLM cosine distance check (~0.15 threshold) against cached queries for instant responses |
| 4 | Session Memory | Conversation history in Redis lists/streams per `session_id`, 24h TTL |
| 5 | Long-Term Memory | Redis Agent Memory Server extracts and persists user facts across sessions |
| 6 | Optimization | Hybrid search (vector + metadata filters), history summarization, graceful degradation |

## Key Technical Constraints

- **Redis Stack is mandatory** — all vector search, caching, session storage, and memory persistence runs through it
- **LLM-agnostic** — must support Gemini, OpenAI, and local Ollama
- **Hybrid search** — combine vector similarity with metadata filters
- **Observability** — expose `/metrics` endpoint for cache hit rates, latency, and cost savings
- **Graceful degradation** — system must remain functional if non-essential components (e.g., long-term memory) fail

---

## Coding Standards & Best Practices

### Code Style

- **Clarity over cleverness.** Code is read far more than it is written. If a line needs a comment to explain what it does, rewrite the line first.
- One responsibility per function. If you need "and" to describe what a function does, split it.
- Name things by what they are, not how they work. `get_relevant_chunks()` not `run_knn_pipeline()`.
- No magic numbers or strings in code — every constant has a named variable with a clear identifier.
- Max function length: ~30 lines. If it's longer, it's doing too much.

### Types & Validation

- All function signatures have type annotations — parameters and return types, no exceptions.
- Use strict types at system boundaries (API inputs, config, external responses). Never trust raw external data.
- Validate early, fail loudly. Catch bad input at the entry point, not deep in business logic.

### Error Handling

- Every error must be actionable. The message should tell you what failed, why, and ideally what to check.
- Never swallow exceptions silently. Log with full context or re-raise.
- Distinguish between expected failures (e.g., no cache hit) and unexpected ones (e.g., Redis connection lost). Handle them differently.
- Graceful degradation is intentional and explicit — not a side effect of suppressed errors.

### Async & Concurrency

- All I/O is async. No blocking calls inside async functions.
- Run independent async operations concurrently (`gather`), not sequentially.
- Never use sleep in async code except for intentional backoff with a comment explaining why.

### Configuration & Secrets

- Every tunable value (thresholds, timeouts, model names, limits) lives in config — not hardcoded.
- Secrets come from environment variables only. No defaults for secrets. Fail on startup if required secrets are missing.
- Config is loaded once at startup, not re-read on every request.

### Logging

- Log at the right level: `DEBUG` for internals, `INFO` for meaningful events, `WARNING` for recoverable issues, `ERROR` for failures requiring attention.
- Every log entry includes enough context to be useful in isolation (e.g., `session_id`, operation name, outcome).
- Never log sensitive data: API keys, raw user queries in production, personal information.
- Use structured logging (key-value or JSON format) — not freeform strings.

### Testing

- Unit tests cover logic in isolation — no network, no database, no filesystem.
- Integration tests cover real interactions with external systems (Redis, LLM APIs).
- Test names describe behavior, not implementation: `test_returns_cached_result_when_similar_query_exists`.
- A test that always passes is worse than no test. Assert on specific outcomes, not just "no exception was raised."

### Test-Driven Development (TDD)

This project follows strict TDD. Every feature, fix, or change starts with a test — not code.

**The cycle (Red → Green → Refactor):**

1. **Red** — Write a failing test that defines the exact behavior you want. Run it. Confirm it fails for the right reason.
2. **Green** — Write the minimum code required to make that test pass. No more, no less.
3. **Refactor** — Clean up the implementation without changing behavior. The test must still pass after refactoring.

Repeat this cycle for every small increment of work.

**Rules:**
- Never write production code without a failing test that justifies it.
- Never write more production code than what is needed to pass the current failing test.
- Never refactor while tests are red.
- If you find a bug, write a test that reproduces it first — then fix it. This prevents the bug from ever silently returning.
- If a feature is hard to test, that is a design signal. Redesign the code so it becomes testable, don't skip the test.

**What to test at each stage:**
- **New feature:** Write tests for the expected behavior, edge cases, and failure modes — before any implementation.
- **Bug fix:** Write a test that fails because of the bug, fix the bug, confirm the test passes.
- **Refactor:** No new tests needed — existing tests are the safety net.
- **Integration point (e.g., new Redis interaction, new LLM call):** Write an integration test that covers the contract with the external system.

**Test structure (Arrange → Act → Assert):**
- **Arrange** — set up the inputs and dependencies.
- **Act** — call the single thing being tested.
- **Assert** — verify the outcome. One logical assertion per test.

Each test must be independent — no shared mutable state between tests, no relying on test execution order.

### Git & Commits

- Commit one logical change at a time. A commit should be explainable in a single sentence.
- Commit message format: `type: short description` — e.g., `feat: add cosine threshold config`, `fix: handle empty chunk list in retriever`.
- Never commit broken code to a shared branch. The branch should be runnable at every commit.
- Reviewed code is better code. Don't merge your own PRs on a shared project.

### Dependencies

- Add a dependency only when it earns its place. Prefer the standard library when it's sufficient.
- Pin dependency versions in production. Unpinned dependencies are a future bug waiting to happen.
- Every new dependency should be understood, not just installed. Know what it does and why it's needed.

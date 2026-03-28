# RAG Deep Dive — Concepts, Trade-offs, and Failure Modes

> A first-principles walkthrough of Retrieval-Augmented Generation mapped to ContextFlow's 7-layer stack. This document covers what RAG is, why each layer exists, the trade-offs at every decision point, how RAG systems fail, and what variations exist in the landscape.

---

## 1. The Core Mental Model

RAG exists because LLMs have two hard limits:

1. **Knowledge cutoff** — they don't know what happened after training.
2. **Context window** — you can't stuff an entire knowledge base into a prompt.

RAG solves both by treating the LLM as a *reasoning engine*, not a *knowledge store*. You retrieve relevant facts at query time, then ask the LLM to reason over them.

The fundamental pipeline is:

```
query → retrieve relevant chunks → LLM generates answer grounded in those chunks
```

This is deceptively simple. The complexity lives in every arrow.

---

## 2. Layer-by-Layer Trade-offs

### 2.1 Layer 1 — Ingestion / Chunking

This is the most underestimated part. Bad chunking ruins everything downstream.

**The core tension:** chunks need to be *small enough* to be precise (so you don't retrieve irrelevant noise) but *large enough* to contain complete thoughts (so the LLM has enough context).

**Chunking strategies:**

| Strategy | How | Best for | Downside |
|---|---|---|---|
| Fixed-size (token) | Split every N tokens | Uniform docs, simple baseline | Cuts mid-sentence, destroys semantic units |
| Sentence-based | Split on `.` or NLP sentence boundaries | General prose | Short sentences lack context |
| Paragraph-based | Split on double newlines | Articles, docs | Paragraph size varies wildly |
| Recursive character | Split on `\n\n`, then `\n`, then ` ` — hierarchy | General-purpose default | Still semantically blind |
| Semantic chunking | Embed sentences, split where embedding cosine distance spikes | High-quality retrieval | Expensive, requires embedding pass |
| Document structure | Split by headings (H1/H2) | Markdown/HTML with structure | Sections vary wildly in length |

ContextFlow uses 500 tokens + overlap. That's a solid default.

**The overlap is critical** — without it, a concept that spans chunk boundaries gets lost. Both sides of the split lose half the idea. Overlap ensures both chunks contain the seam.

**Key trade-off:** chunk size directly affects your embedding quality. A 50-token chunk embeds a narrow concept; a 1000-token chunk embeds a blurry average. `~300-500 tokens` is the sweet spot for most technical docs.

**Metadata is retrieval leverage.** Filename, section heading, position, document version — these are what enable hybrid search later. Every field you store during ingestion is a filter available at query time. Cheap to add during ingestion; expensive to add after (requires re-indexing the entire corpus).

---

### 2.2 Layer 2 — Retrieval

The retrieval step is where most RAG failures actually happen — not at LLM generation.

**What embedding models actually do:** they map text to a point in high-dimensional space where *semantically similar* text is geometrically close. The quality of your embedding model determines the quality of your retrieval ceiling — no LLM can compensate for retrieving the wrong chunks.

**Top-k selection trade-off:**

| k value | Behavior | Risk |
|---|---|---|
| k=1 | Precise but brittle | One bad chunk = bad answer |
| k=3-5 | Balanced coverage | Sweet spot for most use cases |
| k=10+ | Maximum coverage | Feeds the LLM noise; causes hallucinations or confused answers |

You can also use a **reranker** (a second, smaller model that scores each chunk against the query) to fetch k=10 broadly, then filter down to the top 3. This gives you both recall and precision.

**Similarity metrics:**

- **Cosine distance** — measures the angle between vectors. Invariant to vector magnitude. Best for text because it compares *direction* (meaning) not *length* (word count).
- **L2 (Euclidean)** — absolute distance between points. Sensitive to embedding magnitude. Less common for NLP.
- **Dot product** — only meaningful if vectors are normalized (at which point it's equivalent to cosine). Less explicit, more error-prone.

Redis Vector Search supports cosine and L2 — stick with cosine for text.

**The "Lost in the Middle" problem:** LLMs pay more attention to content at the start and end of their context window. If you pass 5 chunks, the most relevant should be first or last — not buried in the middle. This is a real retrieval ordering concern backed by research. When constructing your prompt, place the highest-relevance chunks at position 1 and position N, not position 3.

---

### 2.3 Layer 3 — Semantic Cache

This is the biggest performance win and an underappreciated piece of RAG systems.

**How it works:** when a query comes in, embed it and search the cache for vectors within cosine distance `~0.15`. If you get a hit, return the cached answer directly — no LLM call.

**The threshold is everything:**

| Threshold | Behavior | Hit Rate | Risk |
|---|---|---|---|
| 0.05 | Near-exact match only | Very low (~5%) | Safe but nearly useless |
| 0.10 | Paraphrases match | Moderate (~15-25%) | Good balance |
| 0.15 | Semantically similar questions match | High (~30-40%) | Some wrong answers |
| 0.20+ | Loosely related questions match | Very high | Dangerous — wrong answers served frequently |

**Trade-off: exact match vs semantic match:**
- Redis can do exact hash-based cache (trivial, zero false positives).
- Semantic cache is *approximate* — you're trading some precision for much higher hit rate.
- The right call for a Q&A system: semantic cache is worth it. "How do I install Redis?" and "Redis installation steps" should be the same cache hit. But "How do I install Redis?" and "How do I uninstall Redis?" should NOT.

**What to cache:** cache the full answer, the source chunks used, and the original query vector. This lets you audit why a cached answer was returned and trace it back to the retrieval that generated it.

**Cache poisoning risk:** if a bad LLM answer gets cached (e.g., the LLM hallucinated), that bad answer gets served to every similar query indefinitely. Mitigations:
1. Only cache answers that include at least one valid citation
2. TTL on cache entries so stale answers expire
3. Log every cache hit with its distance value for offline review

---

### 2.4 Layer 4 — Session Memory

The naive approach: include the last N messages in the system prompt. It works, but it has two problems:

1. **Token cost** — full history grows linearly. Long sessions get expensive or hit context limits.
2. **Irrelevance noise** — early messages in a session are often irrelevant to the current question.

**Strategies for managing session memory:**

| Strategy | How | Trade-off |
|---|---|---|
| Last N turns | Include last 5 exchanges | Simple, but forgets early context |
| Summarization | Periodically compress history into a summary | Preserves gist, loses detail |
| Selective retrieval | Embed and vector-search your own history | Complex, but retrieves only relevant turns |
| Redis Streams | Use `XADD`/`XREAD` for ordered history | Great for time-ordered replay |

ContextFlow uses Redis lists/streams with 24h TTL — correct choice. The TTL prevents unbounded growth, and Redis's native list/stream operations make append/read O(1).

**The session_id design matters.** If you use a stable session ID (e.g., per user per topic), memory persists across browser refreshes. If you generate a new one per conversation, it's ephemeral. This is a product decision that affects UX, not just a technical detail.

---

### 2.5 Layer 5 — Long-Term Memory

This is the most experimental layer and the one most likely to fail in interesting ways.

**What it's doing:** extracting *facts about the user* from conversations ("User prefers Python", "User is debugging a Redis cluster") and persisting them as structured data across sessions.

The **Redis Agent Memory Server** approach: an LLM-backed extraction step that identifies factual claims from conversations and writes them to Redis with semantic indexing so they can be retrieved later and injected into future prompts.

**Trade-offs:**

- **Extraction quality** — LLMs aren't perfect at identifying what's worth remembering vs. transient context. You get noise. A user asking "How do I install Redis?" doesn't mean you should store "User doesn't know how to install Redis."
- **Staleness** — "User codes in Go" stored 3 months ago might be wrong now. You need a TTL or decay strategy.
- **Privacy** — you're building a profile of the user. In production this has serious implications (GDPR, right to deletion). For a local system, it's fine.
- **Relevance injection** — you need to retrieve *relevant* memories for each session, not dump all memories into every prompt. Same k-retrieval logic as RAG itself.

**Key insight:** Long-term memory is *RAG applied to your own conversation history*. The pattern is identical — embed facts, store with vectors, retrieve by similarity at query time, inject into prompt. Once you understand the core RAG pattern, long-term memory is a natural extension of the same architecture.

---

### 2.6 Layer 6 — Hybrid Search and Optimization

Pure vector search has a blind spot: **exact terminology**. If a user asks about `XADD` (a Redis command), a semantic search might retrieve chunks about "adding data to streams" rather than the exact API docs for `XADD`.

**Hybrid search:** combine vector similarity with BM25/full-text search (keyword matching). Redis Stack supports both natively via `FT.SEARCH` with vector fields.

**The fusion problem:** how do you combine a vector score and a BM25 score into one ranking?

- **Reciprocal Rank Fusion (RRF):** rank both lists independently, then merge by `1/(k + rank)`. Simple, works well, requires no calibration.
- **Weighted linear combination:** `0.7 * vector_score + 0.3 * bm25_score`. Requires both scores to be on the same scale — they're not. BM25 scores are unbounded; cosine distance is [0, 1]. You'd need normalization, which introduces fragility.

**History summarization:** after long sessions, compress the conversation history into a shorter summary to fit within context limits. This is lossy compression — you preserve the gist but lose specific details. Trigger it based on token count threshold, not turn count.

**Graceful degradation:** if long-term memory is unavailable, the system should still answer questions. If the cache is down, fall through to the LLM. These fallback paths must be designed upfront, not bolted on after the fact. Every non-core component should be independently bypassable.

---

## 3. The RAG Failure Modes You Must Know

These are the ways RAG systems break. Understanding them is more valuable than understanding the happy path.

### 3.1 Retrieval Misses

**What happens:** the right chunk exists in the index but isn't retrieved.

**Why it happens:**
- Chunk size too large → embedding is too blurry to match narrow queries
- Embedding model too weak for the domain (e.g., general-purpose model on highly technical content)
- Query phrasing doesn't align with chunk phrasing (vocabulary mismatch)
- k is too small — the relevant chunk is at position 6 but k=3

**Fixes:** Better chunking, better embedding model, lower similarity threshold, higher k, hybrid search (BM25 catches keyword matches that vectors miss).

### 3.2 Context Contamination

**What happens:** you retrieve k=5 chunks but 2 are irrelevant. The LLM mixes information from relevant and irrelevant chunks, producing a wrong answer.

**Why it happens:**
- k is too high relative to the number of truly relevant chunks
- No reranking step — the raw vector similarity is a noisy signal
- Multiple documents cover similar topics with contradictory information

**Fixes:** Reranker (cross-encoder) to filter after retrieval. Lower k. Better metadata filters to narrow the search space.

### 3.3 Faithfulness Failure

**What happens:** the LLM ignores the retrieved context and uses its training knowledge instead. The answer might be correct in general but isn't grounded in your documents.

**Why it happens:**
- Prompt doesn't explicitly constrain the LLM to the provided context
- The LLM's training knowledge is confident and "louder" than the injected chunks
- Chunks are poorly written or ambiguous, so the LLM falls back to what it "knows"

**Fixes:** Stricter prompt engineering ("Answer ONLY using the provided context. If the context doesn't contain the answer, say so."). Evaluate with a separate judge LLM that checks whether the answer is derivable from the chunks.

### 3.4 Citation Hallucination

**What happens:** the LLM fabricates citations — it references a chunk it wasn't given, or attributes information to the wrong chunk.

**Why it happens:**
- The prompt asks for citations but doesn't enforce a structured format
- The LLM generates plausible-looking but fake chunk references
- The answer blends information from multiple chunks and misattributes

**Fixes:** Force citation format in the prompt (e.g., "cite as [chunk_id]"). Programmatically verify every citation in post-processing — strip any citation that references a chunk not in the retrieved set. Never cache an answer with invalid citations.

### 3.5 Semantic Cache Poisoning

**What happens:** a bad answer gets cached, then served to every user who asks a semantically similar question.

**Why it happens:**
- No quality gate before caching — every LLM response gets cached regardless of correctness
- The cache threshold is too loose, so loosely related queries match the poisoned entry
- No TTL — the bad entry lives forever

**Fixes:** Only cache answers with valid citations. TTL on all cache entries. Expose a cache flush mechanism for manual correction. Log every cache hit for offline review.

### 3.6 Chunk Boundary Cuts

**What happens:** an answer spans two chunks, but only one is retrieved. The response is incomplete or misleading.

**Why it happens:**
- No overlap between chunks — a concept that straddles a boundary is split cleanly
- The embedding of each half-chunk doesn't capture the full concept
- Adjacent chunks aren't considered as a unit

**Fixes:** Overlap (50-100 tokens). Optionally: when a chunk is retrieved, also fetch its neighbors (chunk_index ± 1) and include them as additional context. This "context expansion" step is cheap and effective.

---

## 4. RAG Variations — The Landscape

Understanding where ContextFlow sits relative to other RAG approaches.

| Variant | What it does | When to use | Complexity |
|---|---|---|---|
| **Naive RAG** | Basic retrieve → generate | Baseline. Always start here. | Low |
| **Advanced RAG** | Pre-retrieval query rewriting, post-retrieval reranking, hybrid search | When baseline quality isn't enough | Medium |
| **Modular RAG** | Swappable components (any retriever, any LLM, any memory) | Production systems needing flexibility | Medium-High |
| **Self-RAG** | LLM decides *when* to retrieve vs. answer from its own knowledge | Research; not production-ready | High |
| **HyDE** (Hypothetical Document Embeddings) | Generate a hypothetical answer first, embed *that*, retrieve using the hypothetical embedding | Improves recall for vague or poorly-phrased queries | Medium |
| **RAPTOR** | Recursive summarization tree — summarize chunks, then summarize summaries, building a hierarchy | Very long documents where you need both detail and overview | High |
| **GraphRAG** | Build a knowledge graph from docs, traverse relationships at query time | Complex multi-hop questions ("What connects X to Y?") | Very High |
| **Corrective RAG (CRAG)** | After retrieval, a judge evaluates chunk relevance. If low, triggers a web search fallback. | When your knowledge base might not have the answer | Medium-High |
| **Adaptive RAG** | Routes between different retrieval strategies based on query complexity | High-volume systems with diverse query types | High |

**ContextFlow sits in the Advanced RAG tier** — hybrid search, reranking, semantic caching, session memory, and long-term personalization. This is the right level for learning the full depth of RAG without entering the research frontier.

### HyDE — Worth Understanding in Detail

Standard RAG: embed the *question*, search for similar *document chunks*.

The problem: questions and answers live in different parts of embedding space. "How do I set a TTL?" (a question) and "Use the EXPIRE command to set a time-to-live on any key" (an answer) may not be geometrically close even though they're semantically related.

HyDE: ask the LLM to generate a *hypothetical answer* (without retrieval), embed *that answer*, then search. The hypothetical answer lives in the same embedding space as real document chunks, improving recall.

**Trade-off:** HyDE adds one LLM call before retrieval — it doubles latency on the critical path. It also risks embedding the LLM's hallucinated answer and retrieving chunks that confirm the hallucination (a feedback loop). Use it selectively for queries where standard retrieval consistently underperforms.

---

## 5. Key Takeaways

These are the things that aren't obvious until you build it:

**1. Retrieval quality is the ceiling.** The LLM cannot save bad retrieval. Spend disproportionate time on chunking and embedding quality. A better embedding model beats a better LLM if retrieval is the bottleneck.

**2. Evaluate retrieval separately from generation.** Build a test set: `{question, expected_chunk_ids}`. Measure Hit@K (was the right chunk in the top k results?). This isolates retrieval failures from generation failures. If Hit@5 is below 70%, no prompt tuning will save you.

**3. Semantic cache is your best ROI.** In real workloads, 20-40% of queries are semantically near-duplicates. Serving those from cache is near-instant and costs nothing. This is the optimization that makes a measurable difference at any scale.

**4. Prompting is an engineering constraint, not an afterthought.** How you structure the system prompt — how you inject chunks, ask for citations, constrain the answer to the context — directly determines faithfulness and hallucination rate. The prompt is architecture.

**5. Metadata schema is designed at ingestion, consumed at retrieval.** The fields you store during ingestion are the filters available at query time. If you forget to store document version, you can't filter by it later without re-indexing everything.

**6. Graceful degradation is architecture, not error handling.** You don't add graceful degradation after the system is built. You design for it from the start by making every non-core component independently bypassable.

**7. The hardest problem is knowing when the system is wrong.** A confident wrong answer is worse than "I don't know." A RAG system that detects low-retrieval-confidence situations and says so is more trustworthy than one that always answers.

**8. Long-term memory is just RAG applied to yourself.** Embed user facts, store them, retrieve by query similarity, inject into prompt. It's the same pattern as document retrieval, applied to conversation history.

**9. Hybrid search exists because vectors and keywords fail in complementary ways.** Vectors miss exact terms. Keywords miss semantic meaning. Combining them through RRF gives you both without needing to calibrate score scales.

**10. Every layer is a dial, not a switch.** Chunk size, overlap, top-k, cache threshold, history length, reranker cutoff — these are all continuous parameters that need tuning against real queries. The first implementation is a starting point, not a solution. The feedback loop (chunk → embed → retrieve → evaluate → adjust) is where RAG expertise actually lives.

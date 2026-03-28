# Evaluation Strategy

> You cannot improve what you cannot measure. This document defines how to evaluate every layer of the RAG pipeline — independently and end-to-end — before, during, and after implementation.

---

## 1. Why Evaluate in Layers

RAG failures compound. A retrieval miss causes a generation failure which gets cached and served repeatedly. If you only evaluate end-to-end, you see "bad answer" but have no idea which layer caused it.

```
Evaluation layers (bottom-up):

  Ingestion eval     →  Are chunks well-formed and complete?
       ↓
  Retrieval eval     →  Are the right chunks being found?
       ↓
  Generation eval    →  Is the LLM answer faithful to the chunks?
       ↓
  Cache eval         →  Are cached answers correct and appropriately matched?
       ↓
  End-to-end eval    →  Does the full pipeline produce good answers?
```

Fix problems bottom-up. Never tune prompts if retrieval is broken. Never tune retrieval if chunks are bad.

---

## 2. Evaluation Dataset

Before writing any pipeline code, build a small, curated test set. This is the single most valuable artifact in the project.

### 2.1 Structure

```json
{
  "eval_set": [
    {
      "id": "eval_001",
      "question": "How do I set a TTL on a Redis key?",
      "expected_chunks": ["redis-commands.md§EXPIRE", "redis-commands.md§EXPIREAT"],
      "expected_answer_contains": ["EXPIRE", "seconds", "key"],
      "expected_answer_not_contains": ["I'm not sure", "I don't know"],
      "category": "single-hop",
      "difficulty": "easy"
    },
    {
      "id": "eval_002",
      "question": "What's the difference between EXPIRE and PEXPIRE?",
      "expected_chunks": ["redis-commands.md§EXPIRE", "redis-commands.md§PEXPIRE"],
      "expected_answer_contains": ["seconds", "milliseconds"],
      "category": "comparison",
      "difficulty": "medium"
    },
    {
      "id": "eval_003",
      "question": "How do I set up a Redis cluster with automatic failover and configure Sentinel to monitor it?",
      "expected_chunks": ["redis-cluster.md§Setup", "redis-sentinel.md§Configuration"],
      "expected_answer_contains": ["cluster", "sentinel", "failover"],
      "category": "multi-hop",
      "difficulty": "hard"
    }
  ]
}
```

### 2.2 Coverage Guidelines

Aim for **30-50 questions** that cover:

| Category | What it tests | Example | Target count |
|---|---|---|---|
| Single-hop | One chunk has the answer | "What port does Redis use by default?" | 10-15 |
| Comparison | Answer requires 2+ chunks | "Difference between RDB and AOF?" | 5-10 |
| Multi-hop | Answer spans multiple documents | "How do Sentinel and Cluster interact?" | 5-10 |
| Exact term | Specific command/API name | "What does the XADD command do?" | 5-8 |
| Paraphrase | Semantically same, different wording | "How to make a key expire?" vs "Set TTL on key" | 5-8 |
| Unanswerable | Answer is NOT in the docs | "How do I configure MongoDB replication?" | 3-5 |

**The unanswerable questions are critical.** They test whether the system says "I don't know" or hallucinates an answer. A system that correctly refuses unanswerable queries is more trustworthy than one that always answers.

### 2.3 How to Build the Eval Set

1. Ingest your documentation first
2. Read 10-15 documents yourself and write questions you'd actually ask
3. For each question, note which section(s) contain the answer
4. Include edge cases: typos, abbreviations, vague phrasing
5. Include 3-5 questions where the answer genuinely isn't in the docs

Store the eval set at `tests/eval/eval_set.json`. This file is versioned and grows over time.

---

## 3. Layer Evaluations

### 3.1 Ingestion Evaluation

**What you're checking:** are chunks well-formed, complete, and correctly tagged with metadata?

**Method:** manual review of a sample.

| Check | Pass criteria | How to check |
|---|---|---|
| Chunk completeness | No chunk starts or ends mid-sentence | Read 20 random chunks |
| Overlap correctness | Last ~50 tokens of chunk N appear at start of chunk N+1 | Script: compare chunk boundaries |
| Metadata accuracy | Filename, section, chunk_index are correct | Script: verify metadata matches source |
| Token count accuracy | Stored `token_count` matches actual token count | Script: re-tokenize and compare |
| No content loss | Concatenating all chunks reconstructs the original document (minus overlap duplication) | Script: reconstruct and diff |
| Idempotent re-ingestion | Re-ingesting same file doesn't create duplicates | Ingest twice, count chunks |

**Automation:** write a `scripts/validate_ingestion.py` that runs these checks after every ingestion.

### 3.2 Retrieval Evaluation

**What you're checking:** does the system find the right chunks for a given question?

**Metrics:**

| Metric | Formula | What it tells you |
|---|---|---|
| **Hit@K** | Was at least 1 expected chunk in the top-K results? | Basic recall — did we find anything relevant? |
| **MRR** (Mean Reciprocal Rank) | 1/rank of the first relevant chunk, averaged | How high do relevant chunks rank? |
| **Precision@K** | (relevant chunks in top-K) / K | What fraction of retrieved chunks are relevant? |
| **Recall@K** | (relevant chunks in top-K) / (total relevant chunks) | Did we find all the relevant chunks? |

**Targets:**

| Metric | Minimum acceptable | Good | Excellent |
|---|---|---|---|
| Hit@5 | 70% | 85% | 95%+ |
| MRR | 0.5 | 0.7 | 0.85+ |
| Precision@5 | 0.4 | 0.6 | 0.8+ |

**How to run:**

```
For each eval question:
    1. Embed the question
    2. Run vector search (top-5)
    3. Run hybrid search (top-5)
    4. Compare retrieved chunk_ids against expected_chunks
    5. Compute Hit@5, MRR, Precision@5

Output: table of per-question results + aggregate metrics
```

**What to do with results:**
- Hit@5 < 70%: chunking or embedding problem. Try different chunk sizes, try a better embedding model.
- Hit@5 good but MRR < 0.5: relevant chunks are retrieved but ranked low. Add a reranker.
- Vector search and BM25 both miss: the query vocabulary is too different from the document vocabulary. Try HyDE.
- Vector finds it but BM25 doesn't: expected — vector handles paraphrases. Confirms hybrid search value.
- BM25 finds it but vector doesn't: embedding model weakness for exact terms. Confirms hybrid search value.

### 3.3 Generation Evaluation

**What you're checking:** given the correct chunks, does the LLM produce a faithful, grounded answer?

**Metrics:**

| Metric | What it measures | How to evaluate |
|---|---|---|
| **Faithfulness** | Is every claim in the answer supported by the provided chunks? | LLM judge or manual review |
| **Relevance** | Does the answer actually address the question? | LLM judge or manual review |
| **Citation accuracy** | Do citations reference the correct chunks? | Programmatic — verify chunk_ids exist and contain the cited information |
| **Refusal correctness** | Does the system refuse unanswerable questions? | Check against unanswerable eval questions |

**LLM-as-judge approach:**

Use a separate LLM call (can be the same model, different prompt) to evaluate:

```
Given:
  Question: {question}
  Context chunks: {chunks}
  Generated answer: {answer}

Evaluate:
  1. Faithfulness (1-5): Is every claim in the answer directly supported by the chunks?
  2. Relevance (1-5): Does the answer address the question?
  3. Completeness (1-5): Does the answer cover all relevant information from the chunks?

Return scores and reasoning.
```

**Targets:**

| Metric | Minimum | Good |
|---|---|---|
| Faithfulness | 4.0/5 avg | 4.5/5 |
| Citation accuracy | 90% valid | 98%+ |
| Refusal on unanswerable | 80% correct refusal | 95%+ |

### 3.4 Cache Evaluation

**What you're checking:** are cached answers being served for the right queries?

**Metrics:**

| Metric | What it measures |
|---|---|
| **Hit rate** | % of queries served from cache |
| **Hit accuracy** | % of cache hits where the cached answer is correct for the new query |
| **Distance distribution** | Histogram of cosine distances for cache hits — are we hitting near threshold? |

**How to evaluate:**

```
For each eval question:
    1. Run the query once (populates cache)
    2. Run paraphrased versions of the same question
    3. Check: did the paraphrases hit the cache?
    4. Check: is the cached answer correct for the paraphrase?
    5. Run semantically DIFFERENT questions
    6. Check: did they correctly MISS the cache?
```

**Red flags:**
- Cache hits with distance > 0.12 (near threshold): review these manually. They're the ones most likely to be wrong matches.
- Cache hit rate > 50%: probably threshold is too loose. Tighten and re-evaluate accuracy.
- Cache hit rate < 10%: threshold too tight, or query patterns are too diverse to cache.

### 3.5 End-to-End Evaluation

**What you're checking:** does the full pipeline produce good answers for real questions?

This is the final check, run after all layers pass independently.

```
For each eval question:
    1. Run the full pipeline (cache → retrieve → generate)
    2. Record: answer, citations, from_cache, latency
    3. Check: answer_contains expected keywords
    4. Check: answer_not_contains forbidden phrases
    5. Check: citations reference real chunks
    6. Check: latency within budget
```

**Aggregate dashboard:**

```
Total questions:     50
Correct answers:     42 (84%)
Incorrect answers:   5  (10%)
Correct refusals:    3  (6%)
Cache hits:          12 (24%)
Avg latency (miss):  1.8s
Avg latency (hit):   45ms
Citation accuracy:   96%
```

---

## 4. Evaluation Cadence

| When | What to run | Why |
|---|---|---|
| After changing chunking | Ingestion eval + Retrieval eval | Chunking affects everything downstream |
| After changing embedding model | Retrieval eval | New embedding space, new scores |
| After changing prompts | Generation eval | Prompt changes affect faithfulness and citation |
| After changing cache threshold | Cache eval | Threshold directly controls hit rate vs accuracy |
| Before any "release" | Full end-to-end eval | Confidence check on the whole system |
| After adding new documents | Retrieval eval on new-doc questions | Verify new content is findable |

---

## 5. Eval Tooling

Store evaluation results as JSON for tracking over time:

```json
{
  "run_id": "eval_2026-03-28_v1",
  "timestamp": "2026-03-28T10:30:00Z",
  "config": {
    "chunk_size": 500,
    "overlap": 50,
    "embedding_model": "text-embedding-3-small",
    "cache_threshold": 0.10,
    "top_k": 5
  },
  "results": {
    "retrieval": { "hit_at_5": 0.86, "mrr": 0.72, "precision_at_5": 0.58 },
    "generation": { "faithfulness": 4.3, "citation_accuracy": 0.94 },
    "cache": { "hit_rate": 0.28, "hit_accuracy": 0.97 },
    "end_to_end": { "correct": 0.84, "avg_latency_ms": 1820 }
  }
}
```

Store at `tests/eval/results/`. Each run is a snapshot. Comparing runs tells you whether changes helped or hurt.

---

## 6. The One Rule

**Never tune a layer above without confirming the layer below is working.**

Fix bottom-up: chunking → retrieval → generation → cache → end-to-end.

If Hit@5 is below 70%, no amount of prompt engineering will produce good answers. If faithfulness is below 4.0, caching will just preserve bad answers faster. Each layer has a quality floor that gates the layer above.

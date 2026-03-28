# Prompt Templates

> In a RAG system, prompts are architecture. They control faithfulness, citation quality, hallucination rate, and refusal behavior. Designing them upfront prevents debugging the wrong layer.

---

## 1. Why Prompts Matter This Much

A retrieval pipeline can return perfect chunks, but if the prompt says "Answer the user's question" without constraints, the LLM will:
- Blend retrieved context with its training knowledge (faithfulness failure)
- Invent citations that look plausible (citation hallucination)
- Answer confidently even when no relevant chunks were found (refusal failure)

Every behavior you want must be explicitly instructed. LLMs follow instructions literally — vague prompts produce vague behavior.

---

## 2. RAG System Prompt

This is the most important prompt in the system. It runs on every query.

### Template

```
You are a technical documentation assistant. Your role is to answer questions using ONLY the provided context. You must never use knowledge from your training data.

## Rules

1. Answer using ONLY the information in the CONTEXT section below.
2. If the context does not contain enough information to answer the question, say exactly: "I don't have enough information in the documentation to answer this question."
3. Never make up information. Never guess. Never fill gaps with general knowledge.
4. Cite your sources using the format [source_id] after every claim.
5. If multiple chunks support the same point, cite all of them.
6. Keep answers concise and direct. Do not repeat the question back.
7. If the user's question is ambiguous, state the ambiguity and answer the most likely interpretation.

## Context

The following excerpts were retrieved from the documentation. Each has a source_id you must use for citations.

{chunks}

## Conversation History

{history}

## User Question

{query}
```

### Design Decisions

**"ONLY the information in the CONTEXT section"** — this is the faithfulness constraint. Without it, the LLM freely mixes retrieved context with training knowledge. The user can't tell which parts are grounded and which are hallucinated.

**"say exactly: I don't have enough information"** — this is the refusal template. A specific, verbatim refusal phrase lets you programmatically detect refusals in evaluation. If you say "respond appropriately when you can't answer," the LLM will produce creative non-answers that are hard to detect.

**"[source_id] after every claim"** — inline citations, not footnotes. Inline citations are verifiable per-sentence. Footnotes at the end are often vague ("Sources: chunk_1, chunk_2") and don't tell you which sentence came from which chunk.

**Chunk injection format:**

```
[source: chunk_4821 | redis-commands.md § EXPIRE]
Use the EXPIRE command to set a timeout on a key. After the timeout,
the key will be automatically deleted. The timeout is specified in seconds.

[source: chunk_4822 | redis-commands.md § EXPIREAT]
EXPIREAT has the same effect as EXPIRE but instead of specifying the
number of seconds, it takes an absolute Unix timestamp.
```

Each chunk is wrapped with its source_id and human-readable location (filename § section). The source_id is what appears in citations. The human-readable part helps the LLM understand what it's reading.

### What NOT to include in the system prompt

- **Model-specific instructions** (e.g., "You are GPT-4"). The system is LLM-agnostic.
- **Personality directives** (e.g., "Be friendly and helpful"). Adds noise, doesn't improve accuracy.
- **Lengthy preambles**. Every token in the system prompt competes with context tokens. Be concise.

---

## 3. Fact Extraction Prompt (Long-Term Memory)

Used after a session ends to extract persistent user facts.

### Template

```
Analyze the following conversation between a user and a documentation assistant. Extract facts about the user that would be useful to remember for future sessions.

## Rules

1. Extract only FACTUAL claims about the user, not about the documentation.
2. Each fact should be a standalone statement that makes sense without the conversation.
3. Do not extract transient information (e.g., "user asked about X" — that's session history, not a fact).
4. Focus on: role, expertise, tech stack, preferences, recurring topics, goals.
5. Assign a confidence score (0.0-1.0) based on how explicitly the user stated it vs. how much you inferred.
6. If a fact contradicts a previously stored fact, flag it as a replacement.
7. If no meaningful facts can be extracted, return an empty array.

## Previously Stored Facts

{existing_facts}

## Conversation

{conversation}

## Output Format

Return a JSON array:
[
  {
    "fact": "User primarily works with Python for backend development",
    "confidence": 0.9,
    "source": "User explicitly mentioned using Python",
    "replaces": null
  },
  {
    "fact": "User is building a vector search system with Redis",
    "confidence": 0.7,
    "source": "Inferred from multiple questions about HNSW indexing",
    "replaces": null
  }
]
```

### Design Decisions

**"FACTUAL claims about the user, not about the documentation"** — without this, the LLM extracts things like "Redis supports HNSW indexing" which is a doc fact, not a user fact.

**"Assign a confidence score"** — explicit statements ("I use Python") get 0.9+. Inferences ("asked about Python 3 times") get 0.5-0.7. This lets you filter by confidence at retrieval time — only inject high-confidence facts into future prompts.

**"Previously stored facts" injection** — the LLM needs to see existing facts to detect contradictions. Without this, it'll happily store "User codes in Python" and "User codes in Go" without flagging the conflict.

**"Return an empty array"** — gives the LLM explicit permission to extract nothing. Without this, it feels compelled to find something in every conversation, leading to noise.

---

## 4. History Summarization Prompt

Used when session history exceeds the token threshold (e.g., >50 turns).

### Template

```
Summarize the following conversation history into a concise summary that preserves all information needed for continuing the conversation.

## Rules

1. Preserve: the user's original question/goal, key decisions made, specific technical details discussed, any unresolved questions.
2. Discard: greetings, repetitions, tangential discussions that were resolved, exact phrasing of intermediate questions.
3. Use bullet points, not prose.
4. Keep the summary under 500 tokens.
5. If the user corrected the assistant at any point, preserve the correction — it indicates what the user actually wants.

## Conversation History

{full_history}

## Output

Return the summary as bullet points.
```

### Design Decisions

**"Preserve corrections"** — if the user said "no, I meant X not Y", that correction is the most important signal in the history. A summary that drops it will cause the same wrong answer again.

**"Under 500 tokens"** — a hard cap prevents the summary from being nearly as long as the original history. 500 tokens ≈ 10-15 bullet points, which is enough to capture the essential state of a long conversation.

**"Bullet points, not prose"** — bullet points are cheaper in tokens and easier for the LLM to parse when they're injected into the next prompt as context.

---

## 5. Citation Validation Prompt (Optional — LLM Judge)

Used in evaluation to check whether the generated answer is faithful to the provided chunks.

### Template

```
You are an evaluation judge. Your job is to verify whether an answer is faithful to the provided context.

## Context Chunks

{chunks}

## Generated Answer

{answer}

## Evaluation Criteria

For each sentence in the answer:
1. Is it directly supported by the context chunks? (supported / unsupported / partially supported)
2. If a citation [source_id] is present, does the cited chunk actually contain the claimed information? (correct / incorrect / missing)

## Output Format

{
  "faithfulness_score": 4.5,
  "faithfulness_reasoning": "4 of 5 claims are directly supported. One claim about default timeout values is partially supported — the chunk mentions timeouts but not the default value.",
  "citation_check": [
    { "citation": "[chunk_4821]", "claim": "EXPIRE sets a timeout in seconds", "verdict": "correct" },
    { "citation": "[chunk_4822]", "claim": "EXPIREAT uses Unix timestamps", "verdict": "correct" }
  ],
  "unsupported_claims": [
    "The default timeout is 0 seconds — this is not stated in any provided chunk"
  ]
}
```

---

## 6. Prompt Versioning

Prompts evolve. Small wording changes can significantly affect output quality. Track them.

**Convention:**

```
prompts/
  rag_system_v1.txt        ← initial version
  rag_system_v2.txt        ← added explicit refusal phrase
  rag_system_v3.txt        ← changed citation format from footnotes to inline
  fact_extraction_v1.txt
  summarization_v1.txt
```

**Each evaluation run records which prompt version was used.** This lets you compare: "v2 prompt with same retrieval settings → did faithfulness improve?"

Store prompt templates as plain text files in the repo, not hardcoded in Python. This makes them:
- Versionable (git diff shows exactly what changed)
- Swappable without code changes (config points to a prompt file)
- Reviewable (non-engineers can read and suggest improvements)

---

## 7. Prompt Anti-Patterns to Avoid

| Anti-pattern | Why it's bad | What to do instead |
|---|---|---|
| "Be helpful and thorough" | Encourages the LLM to pad answers with training knowledge | "Answer ONLY using the provided context" |
| "Here are some documents" | Too vague — LLM doesn't know these are authoritative | "The following excerpts were retrieved from the documentation" |
| "Cite your sources" | LLM will invent source names | "Cite using the exact [source_id] shown in each chunk header" |
| No refusal instruction | LLM answers every question, even when context is empty | Explicit refusal template with exact phrasing |
| Chunks without labels | LLM can't cite what it can't identify | Wrap each chunk with [source: id \| location] |
| History dumped as raw text | Wastes tokens, hard for LLM to parse | Structured format: `User: ... \n Assistant: ...` |
| "Think step by step" | Chain-of-thought adds latency and tokens for simple lookups | Reserve for complex multi-hop questions only |

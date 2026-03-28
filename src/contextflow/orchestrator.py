"""Query orchestrator — the main pipeline.

Ties all layers together in sequence:
1. Embed query
2. Check semantic cache
3. Retrieve session history
4. Retrieve long-term user facts
5. Hybrid search (vector + BM25 + metadata filters)
6. Rerank results
7. Build prompt
8. Call LLM
9. Post-process (validate citations, cache answer, update session)
10. Return answer + citations
"""

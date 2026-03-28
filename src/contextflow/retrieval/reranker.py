"""Cross-encoder reranker (optional).

Takes top-k candidates from hybrid search, scores each against the query
using a cross-encoder model, returns re-sorted top results.
"""

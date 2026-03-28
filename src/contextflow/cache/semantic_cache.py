"""Semantic cache.

Before LLM call: embed query, KNN(1) against cache index.
If cosine distance < threshold (default 0.10): return cached answer.
After LLM call: store query vector + answer + source chunks in cache.
"""

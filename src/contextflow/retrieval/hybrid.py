"""Hybrid search — Reciprocal Rank Fusion.

Combine vector search and BM25 results into a single ranked list.
RRF score: sum of 1/(k + rank) across both lists. No score calibration needed.
"""

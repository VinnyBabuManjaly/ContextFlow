"""Redis index definitions.

Define all FT.CREATE schemas: chunk_index, cache_index, memory_index.
Run idempotently on startup (create if not exists).
"""

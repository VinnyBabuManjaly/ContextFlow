"""Tests for Redis index schema definitions.

These tests verify the FT.CREATE field lists WITHOUT executing against Redis.
We test that the schema builder produces the correct field types and configuration.
Actual index creation is tested in integration tests.
"""

import pytest
from redis.commands.search.field import NumericField, TagField, TextField, VectorField

from contextflow.config import Settings
from contextflow.redis.indexes import build_chunk_index_args, build_cache_index_args, build_memory_index_args


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Minimal settings for index schema tests."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from pathlib import Path

    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    return Settings(_config_path=config_path)


def _find_field(fields: list, name: str):
    """Find a field by name in the field list."""
    for f in fields:
        if f.redis_args()[0] == name:
            return f
    return None


class TestChunkIndexSchema:
    """The chunk index is the core of the retrieval system. It must have:
    - text (TEXT) for BM25 keyword search
    - embedding (VECTOR) for KNN similarity search
    - filename (TAG) for metadata filtering
    - section (TEXT) for heading search
    - version (NUMERIC) for version filtering"""

    def test_has_required_fields(self, settings: Settings) -> None:
        fields = build_chunk_index_args(settings)

        assert isinstance(_find_field(fields, "text"), TextField)
        assert isinstance(_find_field(fields, "embedding"), VectorField)
        assert isinstance(_find_field(fields, "filename"), TagField)
        assert isinstance(_find_field(fields, "section"), TextField)
        assert isinstance(_find_field(fields, "version"), NumericField)

    def test_uses_configured_dimension(self, settings: Settings) -> None:
        fields = build_chunk_index_args(settings)
        embedding_field = _find_field(fields, "embedding")
        # The VectorField stores dimension in its redis_args
        redis_args = embedding_field.redis_args()
        # redis_args contains the field spec: name, VECTOR, algorithm, count, ...DIM, value...
        dim_index = redis_args.index("DIM")
        assert redis_args[dim_index + 1] == settings.embedding.dimension

    def test_index_type_matches_config(self, settings: Settings) -> None:
        fields = build_chunk_index_args(settings)
        embedding_field = _find_field(fields, "embedding")
        redis_args = embedding_field.redis_args()
        # The algorithm (FLAT or HNSW) follows the VECTOR keyword
        assert settings.redis.index_type in redis_args


class TestCacheIndexSchema:
    """The cache index enables semantic cache lookup. It only needs a vector
    field for KNN search against cached query embeddings."""

    def test_has_vector_field(self, settings: Settings) -> None:
        fields = build_cache_index_args(settings)
        assert isinstance(_find_field(fields, "query_vector"), VectorField)


class TestMemoryIndexSchema:
    """The memory index stores long-term user facts as vectors for similarity
    retrieval during query time."""

    def test_has_vector_field(self, settings: Settings) -> None:
        fields = build_memory_index_args(settings)
        assert isinstance(_find_field(fields, "fact_vector"), VectorField)

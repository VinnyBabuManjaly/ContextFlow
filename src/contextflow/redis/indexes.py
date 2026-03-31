"""Redis index definitions.

Define all FT.CREATE schemas: chunk_index, cache_index, memory_index.
Run idempotently on startup (create if not exists).

Index schemas from docs/02-System-Design/00-system-design.md Section 5:
- chunk_index: document chunks (vector + full-text + metadata)
- cache_index: semantic cache lookup (vector only)
- memory_index: long-term user facts (vector only)
"""

import logging

from typing import Any, Sequence

import redis.asyncio as aioredis
from redis.commands.search.field import NumericField, TagField, TextField, VectorField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.exceptions import ResponseError

from contextflow.config import Settings

logger = logging.getLogger(__name__)


def _vector_field_args(settings: Settings) -> dict[str, Any]:
    """Common vector field arguments derived from config."""
    args = {
        "TYPE": "FLOAT32",
        "DIM": settings.embedding.dimension,
        "DISTANCE_METRIC": "COSINE",
    }
    if settings.redis.index_type == "HNSW":
        args["M"] = settings.redis.hnsw_m
        args["EF_CONSTRUCTION"] = settings.redis.hnsw_ef_construction
        args["EF_RUNTIME"] = settings.redis.hnsw_ef_runtime
    return args


def build_chunk_index_args(settings: Settings) -> Sequence[VectorField | TextField | TagField | NumericField]:
    """Build the field list for the chunk_index FT.CREATE command.

    Fields:
        text       TEXT     — BM25 full-text search on chunk content
        embedding  VECTOR   — KNN similarity search
        filename   TAG      — metadata filter (exact match)
        section    TEXT     — section heading search
        version    NUMERIC — version filter
    """
    vector_args = _vector_field_args(settings)
    return [
        TextField("text"),
        VectorField("embedding", settings.redis.index_type, vector_args),
        TagField("filename"),
        TextField("section"),
        NumericField("version"),
    ]


def build_cache_index_args(settings: Settings) -> Sequence[VectorField]:
    """Build the field list for the cache_index FT.CREATE command.

    Fields:
        query_vector  VECTOR — KNN search against cached query embeddings
    """
    vector_args = _vector_field_args(settings)
    return [
        VectorField("query_vector", "FLAT", vector_args),
    ]


def build_memory_index_args(settings: Settings) -> Sequence[VectorField]:
    """Build the field list for the memory_index FT.CREATE command.

    Fields:
        fact_vector  VECTOR — KNN search against stored user fact embeddings
    """
    vector_args = _vector_field_args(settings)
    return [
        VectorField("fact_vector", "FLAT", vector_args),
    ]


async def _create_index_if_not_exists(
    client: aioredis.Redis,
    index_name: str,
    prefix: str,
    fields: list[Any],
) -> None:
    """Create a single FT index, skipping if it already exists.

    Redis raises ResponseError with 'Index already exists' when attempting
    to create a duplicate — we catch that specific error to make this
    idempotent.
    """
    try:
        await client.ft(index_name).create_index(
            fields,
            definition=IndexDefinition(prefix=[prefix], index_type=IndexType.HASH),  # type: ignore[no-untyped-call]
        )
        logger.info("Created index: %s (prefix: %s)", index_name, prefix)
    except ResponseError as e:
        if "Index already exists" in str(e):
            logger.debug("Index already exists, skipping: %s", index_name)
        else:
            raise


async def ensure_indexes(client: aioredis.Redis, settings: Settings) -> None:
    """Create all three search indexes if they don't already exist.

    Safe to call on every startup — existing indexes are left untouched.
    """
    await _create_index_if_not_exists(
        client, "chunk_index", "chunk:", build_chunk_index_args(settings)
    )
    await _create_index_if_not_exists(
        client, "cache_index", "cache:", build_cache_index_args(settings)
    )
    await _create_index_if_not_exists(
        client, "memory_index", "memory:", build_memory_index_args(settings)
    )

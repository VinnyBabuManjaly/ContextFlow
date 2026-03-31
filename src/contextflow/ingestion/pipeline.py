"""Ingestion pipeline.

Orchestrates: load file -> chunk -> embed -> store to Redis.

Each chunk is stored as a Redis hash under the key pattern:
    chunk:{doc_id}:{chunk_index}

The doc_id is a SHA-256 hash of the source file content, which makes
re-ingestion idempotent: same content = same hash = skip.
"""

import logging
import struct
import time
from dataclasses import dataclass
from pathlib import Path

import redis.asyncio as aioredis

from contextflow.config import Settings
from contextflow.ingestion.chunker import chunk_document
from contextflow.ingestion.embedder import Embedder
from contextflow.ingestion.loader import load_file

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    """Result of an ingestion operation."""

    chunks_created: int
    filename: str


def _vector_to_bytes(vector: list[float]) -> bytes:
    """Convert a list of floats to a binary blob for Redis VECTOR fields.

    Redis expects vectors as raw bytes in float32 format.
    """
    return struct.pack(f"{len(vector)}f", *vector)


async def ingest_pipeline(
    path: Path,
    redis_client: aioredis.Redis,
    embedder: Embedder,
    settings: Settings,
) -> IngestResult:
    """Run the full ingestion pipeline for a single file.

    Steps:
        1. Load the file from disk
        2. Chunk the document with metadata
        3. Check if already ingested (idempotent via doc_id)
        4. Embed all chunk texts in a batch
        5. Store each chunk as a Redis hash

    Returns:
        IngestResult with the number of new chunks created.
    """
    # Step 1: Load
    document = load_file(path)
    logger.info("Loaded file: %s (%d chars)", document.filename, len(document.text))

    # Step 2: Chunk
    chunks = chunk_document(
        document,
        chunk_size=settings.ingestion.chunk_size,
        chunk_overlap=settings.ingestion.chunk_overlap,
    )
    logger.info("Produced %d chunks from %s", len(chunks), document.filename)

    if not chunks:
        return IngestResult(chunks_created=0, filename=document.filename)

    # Step 3: Check idempotency — if the first chunk's key exists, skip
    doc_id = chunks[0].doc_id
    first_key = f"chunk:{doc_id}:0"
    if await redis_client.exists(first_key):
        logger.info("Already ingested (doc_id=%s), skipping: %s", doc_id[:12], document.filename)
        return IngestResult(chunks_created=0, filename=document.filename)

    # Step 4: Embed all chunk texts
    texts = [chunk.text for chunk in chunks]
    vectors = await embedder.embed_texts(texts)

    # Step 5: Store in Redis
    now = int(time.time())
    for chunk, vector in zip(chunks, vectors):
        key = f"chunk:{chunk.doc_id}:{chunk.chunk_index}"
        mapping = {
            "text": chunk.text,
            "embedding": _vector_to_bytes(vector),
            "filename": chunk.filename,
            "section": chunk.section,
            "chunk_index": chunk.chunk_index,
            "token_count": chunk.token_count,
            "version": 1,
            "indexed_at": now,
        }
        await redis_client.hset(key, mapping=mapping)  # type: ignore[misc]

    logger.info(
        "Stored %d chunks in Redis (doc_id=%s): %s",
        len(chunks), doc_id[:12], document.filename,
    )

    return IngestResult(chunks_created=len(chunks), filename=document.filename)

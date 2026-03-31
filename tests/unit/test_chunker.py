"""Tests for text chunker.

The chunker splits documents into overlapping chunks with metadata. It is the
most important preprocessing step — chunk quality directly determines retrieval
quality, which is the ceiling for the entire system.
"""

from pathlib import Path

import pytest
import tiktoken

from contextflow.ingestion.chunker import Chunk, chunk_document
from contextflow.ingestion.loader import Document

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def sample_doc() -> Document:
    """Load the sample markdown for chunking tests."""
    text = (FIXTURES / "sample.md").read_text()
    return Document(text=text, filename="sample.md", filepath=FIXTURES / "sample.md")


@pytest.fixture
def short_doc() -> Document:
    """A document shorter than chunk_size — should produce one chunk."""
    return Document(text="Hello world.", filename="tiny.md", filepath=Path("tiny.md"))


class TestChunksShortTextIntoSingleChunk:
    """Text shorter than chunk_size should return exactly one chunk.
    No splitting needed."""

    def test_single_chunk(self, short_doc: Document) -> None:
        chunks = chunk_document(short_doc, chunk_size=500, chunk_overlap=50)
        assert len(chunks) == 1
        assert chunks[0].text == "Hello world."


class TestChunksLongTextIntoMultipleChunks:
    """The sample document (~1100 tokens) should produce 2-4 chunks at 500 tokens each."""

    def test_multiple_chunks(self, sample_doc: Document) -> None:
        chunks = chunk_document(sample_doc, chunk_size=500, chunk_overlap=50)
        assert len(chunks) >= 2
        assert len(chunks) <= 5


class TestOverlapExistsBetweenAdjacentChunks:
    """The end of chunk N should overlap with the start of chunk N+1.
    This prevents information loss at chunk boundaries."""

    def test_overlap(self, sample_doc: Document) -> None:
        chunks = chunk_document(sample_doc, chunk_size=500, chunk_overlap=50)
        if len(chunks) < 2:
            pytest.skip("Need at least 2 chunks to test overlap")

        # The last portion of chunk 0 should appear at the start of chunk 1
        chunk_0_end = chunks[0].text[-200:]  # last ~200 chars
        chunk_1_start = chunks[1].text[:200]  # first ~200 chars

        # Find any shared substring of reasonable length
        # At 50 token overlap, we expect ~150-250 shared characters
        shared = set(chunk_0_end.split()) & set(chunk_1_start.split())
        assert len(shared) > 3, "Expected overlap between adjacent chunks"


class TestChunkMetadataHasRequiredFields:
    """Each chunk must carry enough metadata to trace it back to its source
    and position in the original document."""

    def test_required_fields(self, sample_doc: Document) -> None:
        chunks = chunk_document(sample_doc, chunk_size=500, chunk_overlap=50)
        chunk = chunks[0]

        assert chunk.doc_id, "doc_id must not be empty"
        assert chunk.filename == "sample.md"
        assert chunk.section, "section must not be empty for markdown with headings"
        assert chunk.chunk_index == 0
        assert chunk.token_count > 0
        assert chunk.char_offset == 0  # first chunk starts at offset 0


class TestDocIdIsContentHash:
    """doc_id must be a SHA-256 hash of the file content. Same content = same hash.
    This enables idempotent re-ingestion."""

    def test_doc_id_is_deterministic(self, sample_doc: Document) -> None:
        chunks_a = chunk_document(sample_doc, chunk_size=500, chunk_overlap=50)
        chunks_b = chunk_document(sample_doc, chunk_size=500, chunk_overlap=50)
        assert chunks_a[0].doc_id == chunks_b[0].doc_id

    def test_different_content_different_id(self, sample_doc: Document) -> None:
        other = Document(text="Different content", filename="other.md", filepath=Path("other.md"))
        chunks_a = chunk_document(sample_doc, chunk_size=500, chunk_overlap=50)
        chunks_b = chunk_document(other, chunk_size=500, chunk_overlap=50)
        assert chunks_a[0].doc_id != chunks_b[0].doc_id


class TestSectionExtractedFromNearestHeading:
    """For markdown documents, each chunk's section should reflect the nearest
    heading above it. This gives users context about where the chunk lives."""

    def test_section_from_heading(self, sample_doc: Document) -> None:
        chunks = chunk_document(sample_doc, chunk_size=500, chunk_overlap=50)
        # The first chunk should be under "Redis Key Expiration Guide" or
        # "Setting Expiration with EXPIRE"
        sections = [c.section for c in chunks]
        assert any("expir" in s.lower() or "redis" in s.lower() for s in sections)


class TestTokenCountIsAccurate:
    """The stored token_count must match an independent recount with tiktoken."""

    def test_token_count_accurate(self, sample_doc: Document) -> None:
        encoding = tiktoken.get_encoding("cl100k_base")
        chunks = chunk_document(sample_doc, chunk_size=500, chunk_overlap=50)
        for chunk in chunks:
            actual = len(encoding.encode(chunk.text))
            assert chunk.token_count == actual


class TestChunkIndexIsSequential:
    """chunk_index must go 0, 1, 2, ... — needed for ordering and Redis key generation."""

    def test_sequential_index(self, sample_doc: Document) -> None:
        chunks = chunk_document(sample_doc, chunk_size=500, chunk_overlap=50)
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i

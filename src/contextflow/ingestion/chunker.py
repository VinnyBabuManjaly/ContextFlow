"""Text chunker.

Split documents into chunks of ~500 tokens with ~50 token overlap.
Attach metadata: doc_id, filename, section, chunk_index, token_count, char_offset.

Chunking strategy: recursive character splitting.
Split hierarchy: \\n\\n > \\n > " " — respects document structure.
Token counting via tiktoken (cl100k_base encoding).
"""

import hashlib
import re
from dataclasses import dataclass

import tiktoken

from contextflow.ingestion.loader import Document

# Use the same encoding as OpenAI models — cl100k_base covers GPT-4, GPT-3.5,
# and embedding models. Token counts will be close enough for Gemini too.
_encoding = tiktoken.get_encoding("cl100k_base")

# Split hierarchy: prefer splitting on paragraph boundaries, then lines, then spaces.
_SEPARATORS = ["\n\n", "\n", " "]


@dataclass
class Chunk:
    """A chunk of text with metadata for indexing and attribution."""

    text: str
    doc_id: str
    filename: str
    section: str
    chunk_index: int
    token_count: int
    char_offset: int


def _count_tokens(text: str) -> int:
    """Count tokens using tiktoken."""
    return len(_encoding.encode(text))


def _content_hash(text: str) -> str:
    """SHA-256 hash of the text content. Same content = same hash."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_section(text: str, full_text: str, char_offset: int) -> str:
    """Find the nearest markdown heading above this chunk's position.

    Walks backward from char_offset through the full document text,
    looking for lines starting with #.
    """
    # Look at all text up to and including the chunk start
    prefix = full_text[:char_offset + len(text)]
    # Find all markdown headings
    headings = re.findall(r"^(#{1,6})\s+(.+)$", prefix, re.MULTILINE)
    if headings:
        return str(headings[-1][1]).strip()  # return the text of the last heading found
    return ""


def _recursive_split(text: str, chunk_size: int, separators: list[str]) -> list[str]:
    """Recursively split text, preferring higher-level separators.

    Tries to split on \\n\\n first. If a resulting piece is still too long,
    splits that piece on \\n. If still too long, splits on space.
    """
    if _count_tokens(text) <= chunk_size:
        return [text]

    separator = separators[0] if separators else " "
    remaining_separators = separators[1:] if len(separators) > 1 else []

    pieces = text.split(separator)
    chunks: list[str] = []
    current = ""

    for piece in pieces:
        # What would the combined text look like?
        candidate = current + separator + piece if current else piece

        if _count_tokens(candidate) <= chunk_size:
            current = candidate
        else:
            # Flush current if it has content
            if current:
                chunks.append(current)
            # If this single piece is too long, split it further
            if _count_tokens(piece) > chunk_size and remaining_separators:
                sub_chunks = _recursive_split(piece, chunk_size, remaining_separators)
                chunks.extend(sub_chunks)
            else:
                current = piece
                continue
            current = ""

    if current:
        chunks.append(current)

    return chunks


def chunk_document(
    document: Document,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> list[Chunk]:
    """Split a document into overlapping chunks with metadata.

    Args:
        document: The loaded document to chunk.
        chunk_size: Target token count per chunk (default 500).
        chunk_overlap: Token overlap between adjacent chunks (default 50).

    Returns:
        List of Chunk objects with metadata, ordered by position.
    """
    doc_id = _content_hash(document.text)
    full_text = document.text

    # Step 1: Split into non-overlapping segments
    raw_segments = _recursive_split(full_text, chunk_size, _SEPARATORS)

    # Step 2: Add overlap between adjacent chunks
    chunks: list[Chunk] = []
    char_offset = 0

    for i, segment in enumerate(raw_segments):
        # For chunks after the first, prepend overlap from previous segment
        if i > 0 and chunk_overlap > 0:
            prev_text = raw_segments[i - 1]
            prev_tokens = _encoding.encode(prev_text)
            overlap_tokens = prev_tokens[-chunk_overlap:]
            overlap_text = _encoding.decode(overlap_tokens)
            chunk_text = overlap_text + segment
        else:
            chunk_text = segment

        # Calculate char_offset in the original document
        if i == 0:
            char_offset = 0
        else:
            # Find where this segment starts in the original text
            # (approximate — after the previous segment's content)
            seg_pos = full_text.find(segment.strip()[:50], char_offset)
            if seg_pos >= 0:
                char_offset = seg_pos
            else:
                char_offset += len(raw_segments[i - 1])

        section = _extract_section(chunk_text, full_text, char_offset)

        chunks.append(Chunk(
            text=chunk_text,
            doc_id=doc_id,
            filename=document.filename,
            section=section,
            chunk_index=i,
            token_count=_count_tokens(chunk_text),
            char_offset=char_offset,
        ))

    return chunks

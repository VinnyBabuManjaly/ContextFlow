"""Tests for document loader.

The loader reads files from disk and returns Document objects with text and
metadata. It is the first step in the ingestion pipeline — everything downstream
depends on it producing clean, correctly attributed text.
"""

from pathlib import Path

import pytest

from contextflow.ingestion.loader import load_directory, load_file

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class TestLoadsMarkdownFile:
    """Markdown is the primary format for technical documentation."""

    def test_loads_markdown_file(self) -> None:
        doc = load_file(FIXTURES / "sample.md")
        assert "Redis" in doc.text
        assert len(doc.text) > 100


class TestLoadsTxtFile:
    """Plain text is a simple but common format."""

    def test_loads_txt_file(self) -> None:
        doc = load_file(FIXTURES / "short.txt")
        assert "Redis" in doc.text


class TestRejectsUnsupportedFormat:
    """Files with unsupported extensions should be rejected immediately with a
    clear error, not silently return empty or crash in the chunker."""

    def test_rejects_csv(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("a,b,c")
        with pytest.raises(ValueError, match="Unsupported"):
            load_file(csv_file)


class TestLoadsDirectoryRecursively:
    """Given a directory, the loader should find and load all supported files,
    including those in subdirectories."""

    def test_loads_directory_recursively(self) -> None:
        docs = load_directory(FIXTURES)
        # Should find at least sample.md and short.txt
        assert len(docs) >= 2
        filenames = {doc.filename for doc in docs}
        assert "sample.md" in filenames
        assert "short.txt" in filenames


class TestHandlesEmptyFile:
    """An empty file shouldn't crash the loader — it should return an empty
    string and let downstream components decide how to handle it."""

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.md"
        empty.write_text("")
        doc = load_file(empty)
        assert doc.text == ""


class TestReturnsDocumentWithCorrectMetadata:
    """The Document must carry its filename and filepath so chunks can be
    attributed back to the original source."""

    def test_returns_document_with_correct_metadata(self) -> None:
        doc = load_file(FIXTURES / "sample.md")
        assert doc.filename == "sample.md"
        assert doc.filepath == FIXTURES / "sample.md"

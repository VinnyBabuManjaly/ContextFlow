"""Document loader.

Read files from disk: markdown, plain text, PDF.
Return raw text content with source metadata (filename, path).
"""

from dataclasses import dataclass
from pathlib import Path

# Supported file extensions — anything else is rejected.
SUPPORTED_FORMATS = {"md", "txt", "pdf"}


@dataclass
class Document:
    """A loaded document with its text content and source metadata."""

    text: str
    filename: str
    filepath: Path


def load_file(path: Path) -> Document:
    """Load a single file and return a Document.

    Validates the file extension against SUPPORTED_FORMATS.
    Raises ValueError for unsupported formats.
    """
    path = Path(path)
    extension = path.suffix.lstrip(".")

    if extension not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported file format: '.{extension}'. "
            f"Supported formats: {SUPPORTED_FORMATS}"
        )

    text = path.read_text(encoding="utf-8")

    return Document(
        text=text,
        filename=path.name,
        filepath=path,
    )


def load_directory(directory: Path) -> list[Document]:
    """Recursively load all supported files from a directory.

    Skips files with unsupported extensions silently — only returns
    documents for files that match SUPPORTED_FORMATS.
    """
    directory = Path(directory)
    documents: list[Document] = []

    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix.lstrip(".") in SUPPORTED_FORMATS:
            documents.append(load_file(path))

    return documents

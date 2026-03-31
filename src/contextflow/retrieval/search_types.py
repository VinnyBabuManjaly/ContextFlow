"""Shared data types for the retrieval layer."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SearchResult:
    """A single search result from any retrieval method."""

    chunk_id: str
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)

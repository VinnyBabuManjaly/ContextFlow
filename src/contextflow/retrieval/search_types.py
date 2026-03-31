"""Shared data types for the retrieval layer."""

from dataclasses import dataclass, field


@dataclass
class SearchResult:
    """A single search result from any retrieval method."""

    chunk_id: str
    text: str
    score: float
    metadata: dict = field(default_factory=dict)

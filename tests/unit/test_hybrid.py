"""Tests for Reciprocal Rank Fusion.

RRF is a pure function — it takes ranked lists and produces a merged ranking.
No Redis, no I/O, no mocking needed. These tests verify the exact math.

RRF formula: score(d) = sum( 1 / (k + rank_i(d)) ) for each list i
where rank is 1-indexed.
"""

import pytest

from contextflow.retrieval.hybrid import reciprocal_rank_fusion
from contextflow.retrieval.search_types import SearchResult


def _make_result(chunk_id: str, score: float = 0.0) -> SearchResult:
    return SearchResult(chunk_id=chunk_id, text=f"text for {chunk_id}", score=score, metadata={})


class TestRrfMergesTwoRankedLists:
    """Given two ranked lists, RRF should produce a merged ranking where
    items appearing in both lists score higher than items in only one."""

    def test_merges_two_lists(self) -> None:
        list_a = [_make_result("A"), _make_result("B"), _make_result("C")]
        list_b = [_make_result("B"), _make_result("A"), _make_result("D")]

        merged = reciprocal_rank_fusion([list_a, list_b], k=60)

        # B is rank 2 in list_a and rank 1 in list_b -> highest combined score
        # A is rank 1 in list_a and rank 2 in list_b -> same combined score as B
        ids = [r.chunk_id for r in merged]
        # Both A and B should be above C and D
        assert set(ids[:2]) == {"A", "B"}


class TestRrfHandlesDisjointLists:
    """Items appearing in only one list should still appear in the output."""

    def test_disjoint_lists(self) -> None:
        list_a = [_make_result("A"), _make_result("B")]
        list_b = [_make_result("C"), _make_result("D")]

        merged = reciprocal_rank_fusion([list_a, list_b], k=60)

        ids = {r.chunk_id for r in merged}
        assert ids == {"A", "B", "C", "D"}


class TestRrfHandlesEmptyList:
    """One empty list + one non-empty should return the non-empty results."""

    def test_one_empty(self) -> None:
        list_a = [_make_result("A"), _make_result("B")]
        list_b: list[SearchResult] = []

        merged = reciprocal_rank_fusion([list_a, list_b], k=60)

        assert len(merged) == 2
        assert merged[0].chunk_id == "A"


class TestRrfDeduplicates:
    """The same chunk appearing in both lists should appear once in the output
    with its scores summed."""

    def test_deduplicates(self) -> None:
        list_a = [_make_result("A")]
        list_b = [_make_result("A")]

        merged = reciprocal_rank_fusion([list_a, list_b], k=60)

        assert len(merged) == 1
        assert merged[0].chunk_id == "A"
        # Score should be 1/(60+1) + 1/(60+1) = 2/61
        expected = 2 / 61
        assert abs(merged[0].score - expected) < 0.0001


class TestRrfScoreCalculationIsCorrect:
    """For a known input, verify the exact RRF scores."""

    def test_exact_scores(self) -> None:
        list_a = [_make_result("A"), _make_result("B")]
        list_b = [_make_result("B"), _make_result("A")]

        merged = reciprocal_rank_fusion([list_a, list_b], k=60)

        # A: rank 1 in list_a + rank 2 in list_b = 1/61 + 1/62
        # B: rank 2 in list_a + rank 1 in list_b = 1/62 + 1/61
        # A and B have the same score
        expected = 1 / 61 + 1 / 62
        for result in merged:
            assert abs(result.score - expected) < 0.0001

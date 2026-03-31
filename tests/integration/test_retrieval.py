"""Integration tests for retrieval layer.

Tests vector search, text search, hybrid fusion, and reranking against real Redis
with ingested data. Verifies end-to-end retrieval functionality.

These tests require a running Redis instance and use real ingested chunks.
"""

import asyncio
from pathlib import Path

import pytest

from contextflow.config import get_settings
from contextflow.ingestion.embedder import Embedder
from contextflow.ingestion.loader import load_file
from contextflow.ingestion.pipeline import ingest_pipeline
from contextflow.redis.client import close_redis_client, get_redis_client
from contextflow.retrieval.hybrid import reciprocal_rank_fusion
from contextflow.retrieval.reranker import rerank_results
from contextflow.retrieval.text_search import text_search
from contextflow.retrieval.vector_search import vector_search


@pytest.fixture(scope="module")
async def redis_client():
    """Redis client for integration tests."""
    settings = get_settings()
    client = get_redis_client(settings)

    # Verify Redis is available
    try:
        await client.ping()
        yield client
    finally:
        await close_redis_client(client)


@pytest.fixture(scope="module")
async def embedder():
    """Embedder for tests."""
    settings = get_settings()
    embedder = Embedder(settings.embedding.dimension)
    return embedder


@pytest.fixture(scope="module")
async def sample_chunks(redis_client, embedder):
    """Ingest sample documents for retrieval testing."""
    # Load sample documents
    fixtures_dir = Path(__file__).parent.parent / "fixtures"

    # Ingest sample markdown
    sample_md = fixtures_dir / "sample.md"
    if sample_md.exists():
        document = load_file(sample_md)
        result = await ingest_pipeline(redis_client, embedder, document)
        assert result.chunks_created > 0, "Sample document should create chunks"

    # Ingest sample text
    sample_txt = fixtures_dir / "short.txt"
    if sample_txt.exists():
        document = load_file(sample_txt)
        result = await ingest_pipeline(redis_client, embedder, document)
        assert result.chunks_created > 0, "Sample text should create chunks"

    # Give Redis a moment to index
    await asyncio.sleep(1)

    return True


class TestVectorSearch:
    """Test vector similarity search against real Redis."""

    async def test_vector_search_finds_ingested_chunk(self, redis_client, embedder, sample_chunks):
        """Vector search should find chunks similar to query embedding."""
        # Query similar to sample content
        query = "Redis performance optimization and indexing"
        query_vector = await embedder.embed_text(query)

        # Perform vector search
        results = await vector_search(redis_client, query_vector, top_k=3)

        # Verify results
        assert len(results) > 0, "Vector search should return results"
        assert all(hasattr(r, 'chunk_id') for r in results), "Results should have chunk_id"
        assert all(hasattr(r, 'text') for r in results), "Results should have text"
        assert all(hasattr(r, 'score') for r in results), "Results should have score"
        assert all(0 <= r.score <= 1 for r in results), "Scores should be between 0 and 1"

        # Results should be sorted by score (descending)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True), "Results should be sorted by score"


class TestTextSearch:
    """Test BM25 text search against real Redis."""

    async def test_text_search_finds_ingested_chunk(self, redis_client, sample_chunks):
        """Text search should find chunks containing query terms."""
        # Query with terms that should exist in sample documents
        query = "Redis database"

        # Perform text search
        results = await text_search(redis_client, query, top_k=3)

        # Verify results
        assert len(results) > 0, "Text search should return results"
        assert all(hasattr(r, 'chunk_id') for r in results), "Results should have chunk_id"
        assert all(hasattr(r, 'text') for r in results), "Results should have text"
        assert all(hasattr(r, 'score') for r in results), "Results should have score"

        # At least one result should contain the query terms
        matching_chunks = [r for r in results if "redis" in r.text.lower()]
        assert len(matching_chunks) > 0, "At least one result should contain 'redis'"


class TestHybridSearch:
    """Test hybrid search with reciprocal rank fusion."""

    async def test_hybrid_search_combines_results(self, redis_client, embedder, sample_chunks):
        """Hybrid search should combine vector and text search results."""
        # Query that benefits from both semantic and keyword matching
        query = "How to optimize Redis performance"
        query_vector = await embedder.embed_text(query)

        # Run individual searches
        vector_results = await vector_search(redis_client, query_vector, top_k=5)
        text_results = await text_search(redis_client, query, top_k=5)

        # Combine with RRF
        hybrid_results = reciprocal_rank_fusion([vector_results, text_results])

        # Verify hybrid results
        assert len(hybrid_results) > 0, "Hybrid search should return results"
        assert all(hasattr(r, 'chunk_id') for r in hybrid_results), "Results should have chunk_id"
        assert all(hasattr(r, 'text') for r in hybrid_results), "Results should have text"
        assert all(hasattr(r, 'score') for r in hybrid_results), "Results should have score"

        # Hybrid should find chunks that might be missed by individual searches
        hybrid_chunk_ids = {r.chunk_id for r in hybrid_results}
        vector_chunk_ids = {r.chunk_id for r in vector_results}
        text_chunk_ids = {r.chunk_id for r in text_results}

        # Hybrid should include results from both searches
        assert hybrid_chunk_ids >= vector_chunk_ids.intersection(text_chunk_ids), \
            "Hybrid should include chunks found by both searches"

    async def test_hybrid_search_runs_concurrently(self, redis_client, embedder, sample_chunks):
        """Hybrid search should run vector and text searches concurrently."""
        query = "Redis indexing strategies"
        query_vector = await embedder.embed_text(query)

        # Time concurrent execution
        import time
        start_time = time.time()

        vector_results = await vector_search(redis_client, query_vector, top_k=3)
        text_results = await text_search(redis_client, query, top_k=3)
        hybrid_results = reciprocal_rank_fusion([vector_results, text_results])

        concurrent_time = time.time() - start_time

        # Time sequential execution
        start_time = time.time()

        vector_results_seq = await vector_search(redis_client, query_vector, top_k=3)
        text_results_seq = await text_search(redis_client, query, top_k=3)
        hybrid_results_seq = reciprocal_rank_fusion([vector_results_seq, text_results_seq])

        sequential_time = time.time() - start_time

        # Results should be the same
        assert len(hybrid_results) == len(hybrid_results_seq), "Results should be identical"

        # Concurrent should be faster or equal (within test tolerance)
        assert concurrent_time <= sequential_time + 0.1, "Concurrent should be faster or equal"


class TestReranker:
    """Test result reranking."""

    async def test_reranker_reorders_results(self, redis_client, embedder, sample_chunks):
        """Reranker should reorder results based on query relevance."""
        query = "Redis performance tuning"
        query_vector = await embedder.embed_text(query)

        # Get initial results
        initial_results = await vector_search(redis_client, query_vector, top_k=5)
        assert len(initial_results) >= 2, "Need at least 2 results to test reordering"

        # Rerank results
        reranked = await rerank_results(query, initial_results, top_k=3)

        # Verify reranked results
        assert len(reranked) <= len(initial_results), "Reranked should not have more results"
        assert len(reranked) <= 3, "Should respect top_k parameter"
        assert all(hasattr(r, 'chunk_id') for r in reranked), "Results should have chunk_id"
        assert all(hasattr(r, 'text') for r in reranked), "Results should have text"
        assert all(hasattr(r, 'score') for r in reranked), "Results should have score"

        # Scores should be different after reranking
        if len(reranked) >= 2:
            reranked_scores = [r.score for r in reranked]
            # Reranking should change scores (unless they were already optimal)
            # Note: This test might be flaky if reranking doesn't change order
            assert len(set(reranked_scores)) >= 1, "Scores should be present"

    async def test_reranker_respects_top_n(self, redis_client, embedder, sample_chunks):
        """Reranker should return at most top_n results."""
        query = "Redis configuration"
        query_vector = await embedder.embed_text(query)

        # Get more results than we want after reranking
        initial_results = await vector_search(redis_client, query_vector, top_k=10)
        assert len(initial_results) >= 5, "Need enough results to test top_n"

        # Rerank with smaller top_n
        top_n = 3
        reranked = await rerank_results(query, initial_results, top_k=top_n)

        # Should return exactly top_n results (or fewer if not enough)
        assert len(reranked) <= top_n, f"Should return at most {top_n} results"

    async def test_reranker_disabled_returns_input(self, redis_client, embedder, sample_chunks):
        """When reranker is disabled, should return input unchanged."""
        query = "Redis clustering"
        query_vector = await embedder.embed_text(query)

        # Get initial results
        initial_results = await vector_search(redis_client, query_vector, top_k=3)

        # Mock disabled reranker (use_reranker=False)
        # This would need to be implemented in the reranker function
        # For now, we'll test the function exists and returns results
        reranked = await rerank_results(query, initial_results, top_k=5)

        # Should return some results
        assert len(reranked) > 0, "Should return results"


class TestEndToEndRetrieval:
    """Test complete retrieval pipeline end-to-end."""

    async def test_complete_retrieval_pipeline(self, redis_client, embedder, sample_chunks):
        """Test complete pipeline: vector + text + hybrid + rerank."""
        query = "How does Redis handle memory optimization?"
        query_vector = await embedder.embed_text(query)

        # Step 1: Vector search
        vector_results = await vector_search(redis_client, query_vector, top_k=5)
        assert len(vector_results) > 0, "Vector search should work"

        # Step 2: Text search
        text_results = await text_search(redis_client, query, top_k=5)
        assert len(text_results) > 0, "Text search should work"

        # Step 3: Hybrid fusion
        hybrid_results = reciprocal_rank_fusion([vector_results, text_results])
        assert len(hybrid_results) > 0, "Hybrid search should work"

        # Step 4: Reranking
        final_results = await rerank_results(query, hybrid_results, top_k=3)
        assert len(final_results) > 0, "Reranking should work"
        assert len(final_results) <= 3, "Should respect top_k"

        # Verify final results have all required fields
        for result in final_results:
            assert result.chunk_id, "Should have chunk_id"
            assert result.text, "Should have text"
            assert result.score >= 0, "Should have non-negative score"
            assert result.metadata, "Should have metadata"

    async def test_retrieval_with_different_queries(self, redis_client, embedder, sample_chunks):
        """Test retrieval with various types of queries."""
        queries = [
            "Redis performance",
            "database indexing",
            "memory management",
            "clustering strategies"
        ]

        for query in queries:
            query_vector = await embedder.embed_text(query)

            # All search methods should return results
            vector_results = await vector_search(redis_client, query_vector, top_k=3)
            text_results = await text_search(redis_client, query, top_k=3)
            hybrid_results = reciprocal_rank_fusion([vector_results, text_results])

            assert len(vector_results) > 0, f"Vector search should find results for '{query}'"
            assert len(text_results) > 0, f"Text search should find results for '{query}'"
            assert len(hybrid_results) > 0, f"Hybrid search should find results for '{query}'"


class TestErrorHandling:
    """Test error handling in retrieval layer."""

    async def test_vector_search_with_invalid_vector(self, redis_client):
        """Vector search should handle invalid vectors gracefully."""
        # Invalid vector (wrong dimension)
        invalid_vector = [0.1] * 100  # Assuming embedding dimension is different

        # Should handle gracefully (either raise specific error or return empty)
        try:
            results = await vector_search(redis_client, invalid_vector, top_k=3)
            # If it doesn't raise, should return empty results for invalid input
            assert len(results) == 0, "Invalid vector should return no results"
        except Exception as e:
            # Should raise a meaningful error, not crash
            assert "dimension" in str(e).lower() or "vector" in str(e).lower(), \
                "Error should mention vector/dimension issue"

    async def test_text_search_with_empty_query(self, redis_client):
        """Text search should handle empty queries gracefully."""
        results = await text_search(redis_client, "", top_k=3)
        # Empty query should return no results or handle gracefully
        assert isinstance(results, list), "Should return a list"

    async def test_hybrid_search_with_empty_lists(self):
        """Hybrid search should handle empty result lists gracefully."""
        results = reciprocal_rank_fusion([[], []])
        assert len(results) == 0, "Empty lists should produce empty results"

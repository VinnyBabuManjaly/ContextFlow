"""Integration tests for Redis connection and index creation.

These tests require a running Redis Stack instance (docker compose up -d).
They verify actual connectivity, round-trip operations, and that FT.CREATE
commands produce working indexes.
"""

import pytest
import redis.asyncio as aioredis

from contextflow.config import Settings
from contextflow.redis.client import get_redis_client, close_redis_client
from contextflow.redis.indexes import ensure_indexes


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings pointing at the local Docker Redis."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from pathlib import Path

    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    return Settings(_config_path=config_path)


@pytest.fixture
async def client(settings: Settings) -> aioredis.Redis:
    """Async Redis client, cleaned up after each test."""
    c = get_redis_client(settings)
    yield c
    await close_redis_client(c)


@pytest.fixture(autouse=True)
async def clean_redis(client: aioredis.Redis) -> None:
    """Flush the test database before each test to prevent state leaking."""
    await client.flushdb()


class TestPing:
    """Most basic connectivity check. If this fails, Redis isn't running."""

    async def test_ping_succeeds(self, client: aioredis.Redis) -> None:
        result = await client.ping()
        assert result is True


class TestSetGetRoundtrip:
    """Verify that basic Redis operations work through our client."""

    async def test_set_get_roundtrip(self, client: aioredis.Redis) -> None:
        await client.set("test_key", "test_value")
        value = await client.get("test_key")
        assert value == b"test_value"


class TestIndexCreation:
    """Verify that ensure_indexes() creates all three FT indexes and that
    they survive being called twice (idempotent)."""

    async def test_creates_chunk_index(
        self, client: aioredis.Redis, settings: Settings
    ) -> None:
        await ensure_indexes(client, settings)
        # FT.INFO returns index metadata if the index exists, raises otherwise
        info = await client.ft("chunk_index").info()
        assert info is not None

    async def test_creates_cache_index(
        self, client: aioredis.Redis, settings: Settings
    ) -> None:
        await ensure_indexes(client, settings)
        info = await client.ft("cache_index").info()
        assert info is not None

    async def test_creates_memory_index(
        self, client: aioredis.Redis, settings: Settings
    ) -> None:
        await ensure_indexes(client, settings)
        info = await client.ft("memory_index").info()
        assert info is not None

    async def test_indexes_survive_second_ensure_call(
        self, client: aioredis.Redis, settings: Settings
    ) -> None:
        """Calling ensure_indexes() twice must not raise or destroy data.
        This is critical for server restarts — we don't want to drop indexes
        on every startup."""
        await ensure_indexes(client, settings)
        # Store a key to prove data survives
        await client.hset("chunk:test:0", mapping={"text": "hello"})

        # Second call — must not error or drop the key
        await ensure_indexes(client, settings)

        value = await client.hget("chunk:test:0", "text")
        assert value == b"hello"

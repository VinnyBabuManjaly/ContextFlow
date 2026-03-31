"""Integration tests for session memory against real Redis.

Verifies Redis Streams operations, TTL, and multi-turn conversation flow.
Requires a running Redis Stack instance.
"""

from pathlib import Path

import pytest
import redis.asyncio as aioredis

from contextflow.config import Settings
from contextflow.memory.session import SessionMemory
from contextflow.redis.client import close_redis_client, get_redis_client


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    return Settings(_config_path=config_path)


@pytest.fixture
async def client(settings: Settings) -> aioredis.Redis:
    c = get_redis_client(settings)
    yield c
    await close_redis_client(c)


@pytest.fixture(autouse=True)
async def clean_redis(client: aioredis.Redis) -> None:
    await client.flushdb()


@pytest.fixture
def session_memory(client: aioredis.Redis, settings: Settings) -> SessionMemory:
    return SessionMemory(
        client=client,
        max_turns=settings.session.max_turns,
        ttl_seconds=settings.session.ttl_seconds,
    )


class TestSessionTtlSet:
    """After adding turns, TTL should be set on the stream key."""

    async def test_ttl(
        self, client: aioredis.Redis, session_memory: SessionMemory, settings: Settings
    ) -> None:
        sid = session_memory.create_session()
        await session_memory.add_turn(sid, "user", "hello")

        ttl = await client.ttl(f"session:{sid}")
        # TTL should be close to configured value (within a few seconds)
        assert ttl > settings.session.ttl_seconds - 5
        assert ttl <= settings.session.ttl_seconds


class TestMultiTurnConversationFlows:
    """Add user/assistant turns → retrieve → verify ordering and content."""

    async def test_multi_turn(self, session_memory: SessionMemory) -> None:
        sid = session_memory.create_session()

        await session_memory.add_turn(sid, "user", "What is Redis?")
        await session_memory.add_turn(sid, "assistant", "Redis is an in-memory data store.")
        await session_memory.add_turn(sid, "user", "What about TTL?")
        await session_memory.add_turn(sid, "assistant", "TTL sets a timeout on a key.")

        turns = await session_memory.get_recent_turns(sid, n=10)

        assert len(turns) == 4
        assert turns[0].role == "user"
        assert turns[0].content == "What is Redis?"
        assert turns[1].role == "assistant"
        assert turns[2].role == "user"
        assert turns[2].content == "What about TTL?"
        assert turns[3].role == "assistant"


class TestSessionPersistsInRedis:
    """Data should be in Redis Streams, verifiable via XLEN."""

    async def test_persists(
        self, client: aioredis.Redis, session_memory: SessionMemory
    ) -> None:
        sid = session_memory.create_session()
        await session_memory.add_turn(sid, "user", "hello")
        await session_memory.add_turn(sid, "assistant", "hi there")

        stream_len = await client.xlen(f"session:{sid}")
        assert stream_len == 2


class TestDeleteSessionRemovesFromRedis:
    """After deletion, the stream key should not exist."""

    async def test_delete(
        self, client: aioredis.Redis, session_memory: SessionMemory
    ) -> None:
        sid = session_memory.create_session()
        await session_memory.add_turn(sid, "user", "hello")

        await session_memory.delete_session(sid)

        exists = await client.exists(f"session:{sid}")
        assert exists == 0


class TestGetFullHistory:
    """Full history should return all turns in chronological order."""

    async def test_full_history(self, session_memory: SessionMemory) -> None:
        sid = session_memory.create_session()

        for i in range(6):
            role = "user" if i % 2 == 0 else "assistant"
            await session_memory.add_turn(sid, role, f"message {i}")

        turns = await session_memory.get_full_history(sid)

        assert len(turns) == 6
        for i, turn in enumerate(turns):
            assert turn.content == f"message {i}"

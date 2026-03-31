"""Tests for session memory.

All tests mock the Redis client to verify correct Stream commands
(XADD, XREVRANGE, DEL, EXPIRE) are issued.
"""

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from contextflow.memory.session import SessionMemory, Turn


class TestCreateSessionReturnsUniqueId:
    """Two creates must produce different session IDs."""

    async def test_unique_ids(self) -> None:
        mock_client = AsyncMock()
        session = SessionMemory(client=mock_client, max_turns=100, ttl_seconds=86400)

        id1 = session.create_session()
        id2 = session.create_session()

        assert id1 != id2
        assert isinstance(id1, str)
        assert len(id1) > 0


class TestAddTurnAppendsToStream:
    """XADD should be called with the correct key and fields."""

    async def test_xadd_called(self) -> None:
        mock_client = AsyncMock()
        session = SessionMemory(client=mock_client, max_turns=100, ttl_seconds=86400)

        await session.add_turn("sess-1", "user", "hello")

        mock_client.xadd.assert_called_once()
        call_args = mock_client.xadd.call_args
        # First arg is the key
        assert call_args[0][0] == "session:sess-1"
        # Fields should include role and content
        fields = call_args[0][1]
        assert fields["role"] == "user"
        assert fields["content"] == "hello"


class TestAddTurnSetsMaxlen:
    """XADD should enforce MAXLEN to prevent unbounded growth."""

    async def test_maxlen(self) -> None:
        mock_client = AsyncMock()
        session = SessionMemory(client=mock_client, max_turns=100, ttl_seconds=86400)

        await session.add_turn("sess-1", "user", "hello")

        call_kwargs = mock_client.xadd.call_args[1]
        assert call_kwargs.get("maxlen") == 100


class TestAddTurnSetsTtl:
    """EXPIRE should be called on the stream key after adding a turn."""

    async def test_expire_called(self) -> None:
        mock_client = AsyncMock()
        session = SessionMemory(client=mock_client, max_turns=100, ttl_seconds=86400)

        await session.add_turn("sess-1", "user", "hello")

        mock_client.expire.assert_called_once_with("session:sess-1", 86400)


class TestGetRecentTurnsReturnsLastN:
    """Requesting last N from a longer session returns exactly N turns."""

    async def test_returns_last_n(self) -> None:
        mock_client = AsyncMock()
        # Simulate XREVRANGE returning 5 entries (newest first)
        mock_client.xrevrange.return_value = [
            (b"5-0", {b"role": b"assistant", b"content": b"answer 5"}),
            (b"4-0", {b"role": b"user", b"content": b"question 5"}),
            (b"3-0", {b"role": b"assistant", b"content": b"answer 4"}),
            (b"2-0", {b"role": b"user", b"content": b"question 4"}),
            (b"1-0", {b"role": b"assistant", b"content": b"answer 3"}),
        ]
        session = SessionMemory(client=mock_client, max_turns=100, ttl_seconds=86400)

        turns = await session.get_recent_turns("sess-1", n=5)

        assert len(turns) == 5
        mock_client.xrevrange.assert_called_once_with("session:sess-1", count=5)


class TestTurnsAreInChronologicalOrder:
    """Returned turns must be oldest-first (chronological)."""

    async def test_chronological_order(self) -> None:
        mock_client = AsyncMock()
        # XREVRANGE returns newest first, so we expect reversal
        mock_client.xrevrange.return_value = [
            (b"2-0", {b"role": b"assistant", b"content": b"answer"}),
            (b"1-0", {b"role": b"user", b"content": b"question"}),
        ]
        session = SessionMemory(client=mock_client, max_turns=100, ttl_seconds=86400)

        turns = await session.get_recent_turns("sess-1", n=2)

        assert turns[0].role == "user"
        assert turns[0].content == "question"
        assert turns[1].role == "assistant"
        assert turns[1].content == "answer"


class TestDeleteSessionRemovesStream:
    """DEL should be called on the stream key."""

    async def test_delete(self) -> None:
        mock_client = AsyncMock()
        session = SessionMemory(client=mock_client, max_turns=100, ttl_seconds=86400)

        await session.delete_session("sess-1")

        mock_client.delete.assert_called_once_with("session:sess-1")


class TestTurnHasRoleAndContent:
    """Turn dataclass must have role, content fields."""

    def test_turn_fields(self) -> None:
        turn = Turn(role="user", content="hello")
        assert turn.role == "user"
        assert turn.content == "hello"

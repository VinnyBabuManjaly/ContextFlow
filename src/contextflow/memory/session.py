"""Session memory — short-term conversation history.

Redis Streams keyed by session_id.
Append each turn (role, content, timestamp).
Read last N turns for context injection.
MAXLEN ~100 entries, 24h TTL on the key.

Why Streams over Lists?
- Ordered, append-only with native trimming (MAXLEN)
- Rich per-entry metadata (role, content, timestamp)
- XRANGE reads in chronological order
- MAXLEN ~100 prevents unbounded growth
"""

import uuid
from dataclasses import dataclass

import redis.asyncio as aioredis


@dataclass
class Turn:
    """A single conversation turn."""

    role: str
    content: str


SESSION_KEY_PREFIX = "session:"


class SessionMemory:
    """Per-session conversation history backed by Redis Streams.

    Args:
        client: Async Redis client.
        max_turns: Maximum entries in the stream (MAXLEN).
        ttl_seconds: TTL on the stream key.
    """

    def __init__(
        self,
        client: aioredis.Redis,
        max_turns: int = 100,
        ttl_seconds: int = 86_400,
    ) -> None:
        self._client = client
        self._max_turns = max_turns
        self._ttl_seconds = ttl_seconds

    def _key(self, session_id: str) -> str:
        return f"{SESSION_KEY_PREFIX}{session_id}"

    def create_session(self) -> str:
        """Generate a unique session ID."""
        return uuid.uuid4().hex

    async def add_turn(
        self,
        session_id: str,
        role: str,
        content: str,
    ) -> None:
        """Append a turn to the session stream.

        Uses XADD with MAXLEN to auto-trim oldest entries.
        Sets TTL on the key after each write.

        Args:
            session_id: The session to append to.
            role: "user" or "assistant".
            content: The message content.
        """
        key = self._key(session_id)
        await self._client.xadd(
            key,
            {"role": role, "content": content},
            maxlen=self._max_turns,
        )
        await self._client.expire(key, self._ttl_seconds)

    async def get_recent_turns(
        self,
        session_id: str,
        n: int = 10,
    ) -> list[Turn]:
        """Get the last N turns in chronological order.

        Uses XREVRANGE (newest first) then reverses for chronological order.

        Args:
            session_id: The session to read from.
            n: Number of recent turns to return.

        Returns:
            List of Turn objects, oldest first.
        """
        key = self._key(session_id)
        entries = await self._client.xrevrange(key, count=n)

        turns: list[Turn] = []
        for _, fields in entries:
            role = fields[b"role"].decode() if isinstance(fields[b"role"], bytes) else fields[b"role"]
            content = fields[b"content"].decode() if isinstance(fields[b"content"], bytes) else fields[b"content"]
            turns.append(Turn(role=role, content=content))

        # Reverse to get chronological order (oldest first)
        turns.reverse()
        return turns

    async def get_full_history(self, session_id: str) -> list[Turn]:
        """Get the complete session history in chronological order.

        Args:
            session_id: The session to read from.

        Returns:
            List of Turn objects, oldest first.
        """
        key = self._key(session_id)
        entries = await self._client.xrange(key)

        turns: list[Turn] = []
        for _, fields in entries:
            role = fields[b"role"].decode() if isinstance(fields[b"role"], bytes) else fields[b"role"]
            content = fields[b"content"].decode() if isinstance(fields[b"content"], bytes) else fields[b"content"]
            turns.append(Turn(role=role, content=content))

        return turns

    async def delete_session(self, session_id: str) -> None:
        """Delete a session's stream from Redis.

        Args:
            session_id: The session to delete.
        """
        await self._client.delete(self._key(session_id))

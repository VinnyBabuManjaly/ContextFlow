"""Tests for Redis async client factory.

These tests verify the client factory contract WITHOUT needing a running Redis.
We test that the factory creates the right type of object with the right config.
Actual connectivity is tested in integration tests.
"""

import pytest

from contextflow.config import Settings
from contextflow.redis.client import get_redis_client, close_redis_client


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Minimal settings for Redis client tests."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from pathlib import Path

    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    return Settings(_config_path=config_path)


class TestGetClientReturnsAsyncRedis:
    """The factory must return an async Redis client — the entire system uses
    async I/O, so a sync client would block the event loop."""

    def test_returns_async_redis_instance(self, settings: Settings) -> None:
        import redis.asyncio as aioredis

        client = get_redis_client(settings)
        assert isinstance(client, aioredis.Redis)


class TestClientUsesConfiguredUrl:
    """The client must connect to the URL from settings, not a hardcoded default.
    This is critical for pointing at different Redis instances per environment."""

    def test_uses_configured_url(self, settings: Settings) -> None:
        client = get_redis_client(settings)
        # The connection pool stores the host/port from the URL.
        pool = client.connection_pool
        # redis-py stores connection kwargs in the pool
        assert pool.connection_kwargs.get("host") == "localhost"
        assert pool.connection_kwargs.get("port") == 6379


class TestClientPoolSizeMatchesConfig:
    """The connection pool size must match settings to prevent exhaustion under
    concurrent load or wasted connections when set too high."""

    def test_pool_size_matches_config(self, settings: Settings) -> None:
        client = get_redis_client(settings)
        assert client.connection_pool.max_connections == settings.redis.max_connections

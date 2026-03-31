"""Tests for the LLM router.

Verifies provider selection, fallback behavior, and error propagation.
"""

import logging
from unittest.mock import AsyncMock

import pytest

from contextflow.llm.base import LLMProvider, Message
from contextflow.llm.router import LLMRouter


def _make_provider(response: str = "ok") -> LLMProvider:
    """Create a mock LLM provider."""
    provider = AsyncMock(spec=LLMProvider)
    provider.complete = AsyncMock(return_value=response)
    return provider


class TestRoutesToConfiguredProvider:
    """Router should delegate to the primary provider."""

    async def test_routes_to_primary(self) -> None:
        primary = _make_provider("primary answer")
        router = LLMRouter(primary=primary)
        messages = [Message(role="user", content="hello")]

        result = await router.complete(messages)

        assert result == "primary answer"
        primary.complete.assert_called_once()


class TestFallbackOnPrimaryFailure:
    """If primary raises, router should fall back to secondary."""

    async def test_fallback(self) -> None:
        primary = _make_provider()
        primary.complete = AsyncMock(side_effect=RuntimeError("primary down"))
        fallback = _make_provider("fallback answer")
        router = LLMRouter(primary=primary, fallback=fallback)
        messages = [Message(role="user", content="hello")]

        result = await router.complete(messages)

        assert result == "fallback answer"


class TestRaisesWhenNoFallbackAndPrimaryFails:
    """Without fallback, primary failure should propagate."""

    async def test_no_fallback(self) -> None:
        primary = _make_provider()
        primary.complete = AsyncMock(side_effect=RuntimeError("primary down"))
        router = LLMRouter(primary=primary)
        messages = [Message(role="user", content="hello")]

        with pytest.raises(RuntimeError, match="primary down"):
            await router.complete(messages)


class TestLogsFallbackEvent:
    """Falling back should log a WARNING with the primary error."""

    async def test_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        primary = _make_provider()
        primary.complete = AsyncMock(side_effect=RuntimeError("quota exceeded"))
        fallback = _make_provider("fallback answer")
        router = LLMRouter(primary=primary, fallback=fallback)
        messages = [Message(role="user", content="hello")]

        with caplog.at_level(logging.WARNING):
            await router.complete(messages)

        assert any("quota exceeded" in record.message for record in caplog.records)

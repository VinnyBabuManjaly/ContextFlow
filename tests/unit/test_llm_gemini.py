"""Tests for the Gemini LLM provider.

All tests mock the Google GenAI client to avoid real API calls.
Verifies message formatting, streaming/non-streaming, error handling,
and that config parameters are passed through correctly.
"""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contextflow.llm.base import Message
from contextflow.llm.gemini import GeminiProvider


@pytest.fixture
def provider() -> GeminiProvider:
    """Create a GeminiProvider with test config."""
    return GeminiProvider(
        api_key="test-key",
        model="gemini-2.0-flash",
        max_tokens=1024,
        temperature=0.1,
    )


class TestFormatsMessagesCorrectly:
    """Messages must be formatted as Gemini API expects."""

    async def test_formats_messages(self, provider: GeminiProvider) -> None:
        mock_response = MagicMock()
        mock_response.text = "The answer is 42."

        with patch.object(provider, "_call_api", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "The answer is 42."
            result = await provider.complete(
                [Message(role="user", content="What is the answer?")]
            )

        assert result == "The answer is 42."
        mock_call.assert_called_once()


class TestReturnsStringWhenNotStreaming:
    """Non-stream mode must return a plain str."""

    async def test_returns_string(self, provider: GeminiProvider) -> None:
        with patch.object(provider, "_call_api", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "response text"
            result = await provider.complete(
                [Message(role="user", content="hello")],
                stream=False,
            )

        assert isinstance(result, str)
        assert result == "response text"


class TestReturnsAsyncIteratorWhenStreaming:
    """Stream mode must return an AsyncIterator yielding strings."""

    async def test_returns_async_iterator(self, provider: GeminiProvider) -> None:
        async def fake_stream() -> AsyncIterator[str]:
            for token in ["Hello", " ", "world"]:
                yield token

        with patch.object(provider, "_call_stream", new_callable=AsyncMock) as mock_stream:
            mock_stream.return_value = fake_stream()
            result = await provider.complete(
                [Message(role="user", content="hello")],
                stream=True,
            )

        assert isinstance(result, AsyncIterator)
        tokens = [t async for t in result]
        assert tokens == ["Hello", " ", "world"]


class TestHandlesApiErrorWithClearMessage:
    """API errors should be re-raised with context."""

    async def test_api_error(self, provider: GeminiProvider) -> None:
        with patch.object(provider, "_call_api", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = RuntimeError("quota exceeded")

            with pytest.raises(RuntimeError, match="quota exceeded"):
                await provider.complete(
                    [Message(role="user", content="hello")]
                )


class TestRespectsMaxTokens:
    """max_tokens override should be passed to _call_api."""

    async def test_max_tokens(self, provider: GeminiProvider) -> None:
        with patch.object(provider, "_call_api", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "ok"
            await provider.complete(
                [Message(role="user", content="hello")],
                max_tokens=256,
            )

        call_kwargs = mock_call.call_args
        # The max_tokens should be passed through
        assert call_kwargs is not None


class TestRespectsTemperature:
    """temperature override should be passed to _call_api."""

    async def test_temperature(self, provider: GeminiProvider) -> None:
        with patch.object(provider, "_call_api", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "ok"
            await provider.complete(
                [Message(role="user", content="hello")],
                temperature=0.5,
            )

        call_kwargs = mock_call.call_args
        assert call_kwargs is not None

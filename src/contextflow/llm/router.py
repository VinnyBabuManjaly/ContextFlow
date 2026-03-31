"""LLM router.

Select provider based on config. Route calls to the active provider.
Optional fallback: if primary fails, try secondary.
"""

import logging
from collections.abc import AsyncIterator

from contextflow.llm.base import LLMProvider, Message

logger = logging.getLogger(__name__)


class LLMRouter:
    """Routes LLM calls to a primary provider with optional fallback.

    Args:
        primary: The main LLM provider.
        fallback: Optional backup provider used when primary fails.
    """

    def __init__(
        self,
        primary: LLMProvider,
        fallback: LLMProvider | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    async def complete(
        self,
        messages: list[Message],
        *,
        stream: bool = False,
        max_tokens: int = 1024,
        temperature: float = 0.1,
    ) -> str | AsyncIterator[str]:
        """Route completion to primary, falling back if configured.

        Args:
            messages: Conversation messages.
            stream: Whether to stream the response.
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.

        Returns:
            Response string or async iterator of token strings.

        Raises:
            Exception: If primary fails and no fallback is configured.
        """
        try:
            return await self._primary.complete(
                messages, stream=stream, max_tokens=max_tokens, temperature=temperature,
            )
        except Exception as exc:
            if self._fallback is None:
                raise

            logger.warning(
                "Primary LLM failed, falling back: %s", str(exc),
            )
            return await self._fallback.complete(
                messages, stream=stream, max_tokens=max_tokens, temperature=temperature,
            )

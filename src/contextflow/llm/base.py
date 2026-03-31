"""Abstract LLM interface.

All providers (Gemini, OpenAI, Ollama) implement this interface.
Single method: complete(messages, stream) -> str | AsyncIterator[str]
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass
class Message:
    """A single message in a conversation."""

    role: str
    content: str


class LLMProvider(ABC):
    """Base class for all LLM providers.

    Subclasses must implement complete() to handle both streaming
    and non-streaming responses.
    """

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        *,
        stream: bool = False,
        max_tokens: int = 1024,
        temperature: float = 0.1,
    ) -> str | AsyncIterator[str]:
        """Generate a completion from the given messages.

        Args:
            messages: Conversation messages (system, user, assistant).
            stream: If True, return an async iterator yielding tokens.
            max_tokens: Maximum tokens in the response.
            temperature: Sampling temperature.

        Returns:
            Full response string, or async iterator of token strings.
        """

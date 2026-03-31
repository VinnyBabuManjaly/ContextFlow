"""Gemini provider.

Implements the base LLM interface for Google Gemini API.
Uses the google-genai SDK for async completions.

The provider has two internal methods that are the injection points for mocking:
- _call_api(): non-streaming completion
- _call_stream(): streaming completion (returns async iterator)
"""

from collections.abc import AsyncIterator

from contextflow.llm.base import LLMProvider, Message


class GeminiProvider(LLMProvider):
    """Google Gemini LLM provider.

    Args:
        api_key: Gemini API key.
        model: Model name (e.g., "gemini-2.0-flash").
        max_tokens: Default max output tokens.
        temperature: Default sampling temperature.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        max_tokens: int = 1024,
        temperature: float = 0.1,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def _format_messages(self, messages: list[Message]) -> tuple[str | None, list[dict]]:
        """Convert Messages to Gemini format.

        Separates the system instruction from conversation contents.
        """
        system_instruction: str | None = None
        contents: list[dict] = []

        for msg in messages:
            if msg.role == "system":
                system_instruction = msg.content
            else:
                role = "model" if msg.role == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": msg.content}]})

        return system_instruction, contents

    async def _call_api(
        self,
        messages: list[Message],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Call Gemini API for a non-streaming completion."""
        from google import genai

        system_instruction, contents = self._format_messages(messages)

        client = genai.Client(api_key=self._api_key)
        config = genai.types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
            system_instruction=system_instruction,
        )

        response = await client.aio.models.generate_content(
            model=self._model,
            contents=contents,
            config=config,
        )
        return response.text

    async def _call_stream(
        self,
        messages: list[Message],
        max_tokens: int,
        temperature: float,
    ) -> AsyncIterator[str]:
        """Call Gemini API for a streaming completion."""
        from google import genai

        system_instruction, contents = self._format_messages(messages)

        client = genai.Client(api_key=self._api_key)
        config = genai.types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
            system_instruction=system_instruction,
        )

        async for chunk in await client.aio.models.generate_content_stream(
            model=self._model,
            contents=contents,
            config=config,
        ):
            if chunk.text:
                yield chunk.text

    async def complete(
        self,
        messages: list[Message],
        *,
        stream: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str | AsyncIterator[str]:
        """Generate a completion from the given messages."""
        effective_max_tokens = max_tokens if max_tokens is not None else self._max_tokens
        effective_temperature = temperature if temperature is not None else self._temperature

        if stream:
            return await self._call_stream(messages, effective_max_tokens, effective_temperature)

        return await self._call_api(messages, effective_max_tokens, effective_temperature)

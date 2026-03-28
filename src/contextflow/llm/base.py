"""Abstract LLM interface.

All providers (OpenAI, Gemini, Ollama) implement this interface.
Single method: complete(messages, stream) -> str | AsyncIterator[str]
"""

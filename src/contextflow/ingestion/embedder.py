"""Embedding generator.

Convert text chunks to vectors using the configured embedding model.
Supports Gemini, OpenAI, and local sentence-transformers through a common interface.

The Embedder class provides embed_text() and embed_texts() methods. The actual
API call is delegated to _call_api(), which can be overridden in tests or
replaced with different provider implementations.
"""


class Embedder:
    """Text-to-vector embedding generator.

    Args:
        dimension: Expected vector dimension (must match the model output).
    """

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension

    async def _call_api(self, texts: list[str]) -> list[list[float]]:
        """Call the embedding API. Override this in subclasses or mocks.

        This base implementation raises NotImplementedError — concrete
        providers (GeminiEmbedder, OpenAIEmbedder) override this method.
        """
        raise NotImplementedError("Subclass must implement _call_api")

    async def embed_text(self, text: str) -> list[float]:
        """Embed a single text and return its vector.

        Raises ValueError if text is empty.
        """
        if not text.strip():
            raise ValueError("Cannot embed empty text")

        vectors = await self.embed_texts([text])
        return vectors[0]

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts and return their vectors.

        Args:
            texts: List of strings to embed.

        Returns:
            List of vectors, one per input text. Each vector has
            exactly `self.dimension` floats.
        """
        return await self._call_api(texts)

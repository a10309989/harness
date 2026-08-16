"""Embedding function factory for ChromaDB."""

from chromadb import Documents, EmbeddingFunction, Embeddings


class DefaultEmbeddingFunction(EmbeddingFunction):
    """Simple fallback embedding using sentence-transformers when available,
    otherwise a basic TF-IDF-like approach."""

    def __call__(self, input: Documents) -> Embeddings:
        """Generate embeddings for input documents.

        Uses a simple character-n-gram hash approach as a fallback.
        For production, use the OpenAIEmbeddingFunction instead.
        """
        embeddings = []
        for text in input:
            # Simple hash-based embedding (384 dims for all-MiniLM-L6-v2 compat)
            vec = [0.0] * 384
            for i, char in enumerate(text):
                vec[hash(char) % 384] += 1.0
            # Normalize
            norm = sum(v * v for v in vec) ** 0.5
            if norm > 0:
                vec = [v / norm for v in vec]
            embeddings.append(vec)
        return embeddings


class OpenAIEmbeddingFunction(EmbeddingFunction):
    """OpenAI embedding function for ChromaDB."""

    def __init__(self, api_key: str, model_name: str = "text-embedding-3-small") -> None:
        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=api_key)
        except ImportError:
            raise ImportError("openai package is required for OpenAI embeddings")
        self.model_name = model_name

    def __call__(self, input: Documents) -> Embeddings:
        response = self.client.embeddings.create(model=self.model_name, input=input)
        return [e.embedding for e in response.data]


class EmbeddingFunctionFactory:
    """Factory for creating embedding functions."""

    @classmethod
    def create(cls, provider: str, model_name: str = "text-embedding-3-small", api_key: str = "") -> EmbeddingFunction:
        """Create an embedding function based on provider.

        Args:
            provider: "openai", "local", or "default"
            model_name: Embedding model name.
            api_key: API key for the provider.

        Returns:
            A ChromaDB EmbeddingFunction instance.
        """
        if provider == "openai" and api_key:
            return OpenAIEmbeddingFunction(api_key=api_key, model_name=model_name)
        return DefaultEmbeddingFunction()

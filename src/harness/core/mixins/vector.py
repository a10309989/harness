"""VectorMixin — ChromaDB semantic search and retrieval.

Note: Full functionality requires Phase 3 vector store implementation.
This mixin provides the interface and will be wired to ChromaDB.
"""

from abc import ABC
from typing import Any


class VectorMixin(ABC):
    """Capability: vector store for semantic search and retrieval.

    Backed by ChromaDB. Each agent has its own collection for
    domain-specific semantic search (test cases, error patterns, etc.).
    """

    vector_store: Any = None  # ChromaStore — set during _init_vector
    _collection_name: str = ""
    _embedding_model: str = ""
    _embedding_provider: str = ""

    def _init_vector(
        self,
        collection_name: str,
        embedding_model: str = "text-embedding-3-small",
        embedding_provider: str = "openai",
        persist_directory: str = "data/chroma",
    ) -> None:
        """Initialize vector store capabilities.

        Args:
            collection_name: ChromaDB collection name for this agent.
            embedding_model: Embedding model to use.
            embedding_provider: Provider for embeddings.
            persist_directory: Directory for ChromaDB persistence.
        """
        self._collection_name = collection_name
        self._embedding_model = embedding_model
        self._embedding_provider = embedding_provider
        self._persist_directory = persist_directory
        # Full initialization deferred to Phase 3
        self.vector_store = None

    async def embed_and_store(self, texts: list[str], metadatas: list[dict] | None = None, ids: list[str] | None = None) -> list[str]:
        """Embed texts and store in the vector collection.

        Args:
            texts: Texts to embed and store.
            metadatas: Optional metadata for each text.
            ids: Optional IDs for each document.

        Returns:
            List of document IDs.
        """
        if self.vector_store is None:
            return []
        return await self.vector_store.add(documents=texts, metadatas=metadatas, ids=ids)

    async def semantic_search(self, query: str, top_k: int = 5, metadata_filter: dict | None = None) -> list[dict]:
        """Search the vector collection for semantically similar documents.

        Args:
            query: The search query.
            top_k: Number of results to return.
            metadata_filter: Optional metadata filter.

        Returns:
            List of {document, metadata, score, id} dicts.
        """
        if self.vector_store is None:
            return []
        return await self.vector_store.query(query_texts=[query], n_results=top_k, where=metadata_filter)

    def attach_vector_collection(self, collection_config: Any) -> None:
        """Attach an additional vector collection (used by SkillBinder).

        Args:
            collection_config: VectorCollectionConfig from a Skill.
        """
        # Will be implemented when ChromaDB layer is ready
        pass

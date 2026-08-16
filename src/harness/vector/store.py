"""ChromaStore — ChromaDB client wrapper with collection lifecycle management."""

import chromadb
from chromadb.config import Settings as ChromaSettings

from harness.vector.embeddings import EmbeddingFunctionFactory


class ChromaStore:
    """Wrapper around ChromaDB client with collection lifecycle management.

    Provides add, query, get, delete, and count operations on named
    collections with automatic embedding generation.
    """

    def __init__(
        self,
        collection_name: str,
        persist_directory: str = "data/chroma",
        embedding_model: str = "text-embedding-3-small",
        embedding_provider: str = "openai",
    ) -> None:
        self.collection_name = collection_name
        self.persist_directory = persist_directory

        self.client = chromadb.PersistentClient(
            path=persist_directory,
            settings=ChromaSettings(anonymized_telemetry=False),
        )

        self.embedding_fn = EmbeddingFunctionFactory.create(
            provider=embedding_provider,
            model_name=embedding_model,
        )

        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )

    def add(
        self,
        documents: list[str],
        metadatas: list[dict] | None = None,
        ids: list[str] | None = None,
    ) -> list[str]:
        """Add documents to the collection.

        Args:
            documents: List of text documents to embed and store.
            metadatas: Optional metadata dicts (one per document).
            ids: Optional document IDs (auto-generated if omitted).

        Returns:
            List of document IDs.
        """
        if ids is None:
            import ulid
            ids = [str(ulid.new()) for _ in documents]

        self.collection.add(
            documents=documents,
            metadatas=metadatas,
            ids=ids,
        )
        return ids

    def query(
        self,
        query_texts: list[str],
        n_results: int = 5,
        where: dict | None = None,
        where_document: dict | None = None,
    ) -> dict:
        """Semantic search over the collection.

        Args:
            query_texts: Query texts to search with.
            n_results: Number of results per query.
            where: Metadata filter dict.
            where_document: Document content filter dict.

        Returns:
            Dict with keys: ids, distances, documents, metadatas.
        """
        return self.collection.query(
            query_texts=query_texts,
            n_results=n_results,
            where=where,
            where_document=where_document,
        )

    def get(self, ids: list[str]) -> dict:
        """Retrieve documents by ID.

        Args:
            ids: List of document IDs.

        Returns:
            Dict with keys: ids, documents, metadatas.
        """
        return self.collection.get(ids=ids)

    def delete(self, ids: list[str]) -> None:
        """Remove documents by ID.

        Args:
            ids: List of document IDs to delete.
        """
        self.collection.delete(ids=ids)

    def count(self) -> int:
        """Return the number of documents in the collection."""
        return self.collection.count()

    def update_metadata(self, ids: list[str], metadatas: list[dict]) -> None:
        """Update metadata for existing documents.

        Args:
            ids: Document IDs to update.
            metadatas: New metadata dicts.
        """
        self.collection.update(ids=ids, metadatas=metadatas)

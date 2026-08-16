"""Retriever — semantic and hybrid retrieval strategies."""

from dataclasses import dataclass

from harness.vector.store import ChromaStore


@dataclass
class RetrievalResult:
    """A single retrieval result."""

    document: str
    metadata: dict
    score: float
    id: str


class Retriever:
    """Semantic + hybrid retrieval over a ChromaDB collection.

    Supports:
    - Pure semantic (embedding) search
    - Keyword-boosted hybrid search
    """

    def __init__(self, store: ChromaStore) -> None:
        self.store = store

    async def semantic_search(
        self,
        query: str,
        top_k: int = 5,
        metadata_filter: dict | None = None,
    ) -> list[RetrievalResult]:
        """Pure semantic (embedding-based) search.

        Args:
            query: Search query string.
            top_k: Number of results to return.
            metadata_filter: Optional metadata where clause.

        Returns:
            List of RetrievalResult sorted by relevance.
        """
        result = self.store.query(
            query_texts=[query],
            n_results=top_k,
            where=metadata_filter,
        )

        items = []
        ids = result.get("ids", [[]])[0]
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]

        for i in range(len(ids)):
            items.append(RetrievalResult(
                id=ids[i],
                document=docs[i] if i < len(docs) else "",
                metadata=metas[i] if i < len(metas) else {},
                score=1.0 - distances[i] if i < len(distances) else 0.0,  # Convert distance to similarity
            ))

        return items

    async def hybrid_search(
        self,
        query: str,
        top_k: int = 5,
        keyword_weight: float = 0.3,
        metadata_filter: dict | None = None,
    ) -> list[RetrievalResult]:
        """Combine semantic search with keyword-based boosting.

        Args:
            query: Search query string.
            top_k: Number of results.
            keyword_weight: Weight for keyword matching (0.0-1.0).
            metadata_filter: Optional metadata filter.

        Returns:
            List of RetrievalResult with hybrid scoring.
        """
        # Get more results for re-ranking
        results = await self.semantic_search(query, top_k=top_k * 2, metadata_filter=metadata_filter)

        # Simple keyword boost: check if query terms appear in document
        query_terms = set(query.lower().split())
        for r in results:
            doc_terms = set(r.document.lower().split())
            overlap = len(query_terms & doc_terms) / max(len(query_terms), 1)
            r.score = (1 - keyword_weight) * r.score + keyword_weight * overlap

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]

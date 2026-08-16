"""KnowledgeBase abstract base and concrete implementation."""

import json
import logging
from pathlib import Path

import yaml

from harness.models.knowledge import KnowledgeCategory, KnowledgeEntry
from harness.vector.retriever import RetrievalResult, Retriever
from harness.vector.store import ChromaStore

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """A domain knowledge base backed by ChromaDB for semantic retrieval.

    Each knowledge base has a name, a source directory of YAML/JSON/Markdown
    files, and a ChromaDB collection for vector-indexed search.
    """

    def __init__(
        self,
        name: str,
        store: ChromaStore,
        source_path: str = "",
    ) -> None:
        self.name = name
        self.store = store
        self.source_path = source_path
        self.retriever = Retriever(store)
        self.entries: dict[str, KnowledgeEntry] = {}

    async def load(self, source_path: str | None = None) -> int:
        """Load knowledge entries from YAML/JSON files.

        Args:
            source_path: Directory to load from (overrides configured path).

        Returns:
            Number of entries loaded.
        """
        path = Path(source_path or self.source_path)
        if not path.exists():
            logger.warning(f"Knowledge source path not found: {path}")
            return 0

        count = 0
        for file_path in path.rglob("*"):
            if file_path.suffix in (".yaml", ".yml"):
                count += self._load_yaml(file_path)
            elif file_path.suffix == ".json":
                count += self._load_json(file_path)
        return count

    def _load_yaml(self, file_path: Path) -> int:
        """Load knowledge entries from a YAML file."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return self._ingest_entries(data, str(file_path))
        except Exception as e:
            logger.error(f"Failed to load knowledge from {file_path}: {e}")
            return 0

    def _load_json(self, file_path: Path) -> int:
        """Load knowledge entries from a JSON file."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return self._ingest_entries(data, str(file_path))
        except Exception as e:
            logger.error(f"Failed to load knowledge from {file_path}: {e}")
            return 0

    def _ingest_entries(self, data: list | dict, source_file: str) -> int:
        """Ingest raw data as knowledge entries and index them.

        Only newly added entries (not already in self.entries) are indexed
        to avoid duplicate vector embeddings.
        """
        if not isinstance(data, list):
            data = [data]

        new_entries: dict[str, KnowledgeEntry] = {}
        for entry_data in data:
            if not isinstance(entry_data, dict):
                continue

            entry = KnowledgeEntry(
                id=entry_data.get("id", ""),
                title=entry_data.get("title", "Untitled"),
                content=entry_data.get("content", json.dumps(entry_data, ensure_ascii=False)),
                category=entry_data.get("category", KnowledgeCategory.GENERAL),
                tags=entry_data.get("tags", []),
                source_file=source_file,
                metadata=entry_data.get("metadata", {}),
            )

            if not entry.id:
                from harness.utils.id_gen import generate_id
                entry.id = generate_id()

            # Only collect entries we haven't seen before
            if entry.id not in self.entries:
                new_entries[entry.id] = entry

        # Merge new entries into the full entry set
        self.entries.update(new_entries)

        # Only index newly added entries to avoid duplicates in ChromaDB
        count = len(new_entries)
        if count > 0:
            docs = [e.content for e in new_entries.values()]
            metas = [
                {"title": e.title, "category": str(e.category), "tags": ",".join(e.tags)}
                for e in new_entries.values()
            ]
            ids = list(new_entries.keys())
            self.store.add(documents=docs, metadatas=metas, ids=ids)

        return count

    async def query(self, query_text: str, top_k: int = 5) -> list[RetrievalResult]:
        """Semantic query over the knowledge base.

        Args:
            query_text: Search query.
            top_k: Number of results.

        Returns:
            List of RetrievalResult sorted by relevance.
        """
        return await self.retriever.semantic_search(query_text, top_k=top_k)

    async def add_entry(self, entry: KnowledgeEntry) -> str:
        """Add a single entry and index it.

        Args:
            entry: The knowledge entry to add.

        Returns:
            The entry ID.
        """
        from harness.utils.id_gen import generate_id

        if not entry.id:
            entry.id = generate_id()

        self.entries[entry.id] = entry
        self.store.add(
            documents=[entry.content],
            metadatas=[{"title": entry.title, "category": str(entry.category)}],
            ids=[entry.id],
        )
        return entry.id

    async def delete_entry(self, entry_id: str) -> None:
        """Remove an entry from the KB and index.

        Args:
            entry_id: ID of the entry to delete.
        """
        self.entries.pop(entry_id, None)
        self.store.delete(ids=[entry_id])

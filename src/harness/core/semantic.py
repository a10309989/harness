"""Semantic intent classifier — embedding-based routing layer.

Lies between the keyword classifier and the LLM classifier in the master's
chain. It embeds the message and scores similarity against each intent's
capability keywords; the highest score above ``threshold`` wins. When no
``embed`` callable is available (no embedding provider configured) it returns
None and the chain falls through to the LLM classifier.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from harness.core.master import IntentClassifier
from harness.models.intent import IntentCategory, IntentClassification

Embed = Callable[[str], Awaitable[list[float]]]


class SemanticIntentClassifier(IntentClassifier):
    """Embedding-similarity intent classifier (optional enhancement)."""

    def __init__(
        self,
        embed: Embed | None,
        keyword_map: dict[IntentCategory, list[str]] | None = None,
        threshold: float = 0.55,
    ) -> None:
        self._embed = embed
        self._keyword_map = keyword_map or {}
        self._threshold = threshold
        self._cache: dict[IntentCategory, list[float]] = {}

    async def _do_classify(self, message: str, context: dict) -> IntentClassification | None:
        if self._embed is None or not self._keyword_map:
            return None
        try:
            query = await self._embed(message)
        except Exception:
            return None
        best: tuple[float, IntentCategory | None] = (0.0, None)
        for intent, keywords in self._keyword_map.items():
            if not keywords:
                continue
            vec = self._cache.get(intent)
            if vec is None:
                # Embed the concatenated keywords once; cache per intent.
                vec = await self._embed(" ".join(keywords))
                self._cache[intent] = vec
            score = _cosine(query, vec)
            if score > best[0]:
                best = (score, intent)
        score, intent = best
        if intent is not None and score >= self._threshold:
            return IntentClassification(
                intent=intent, confidence=score, reasoning="semantic match"
            )
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0

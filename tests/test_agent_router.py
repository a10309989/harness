"""Multi-version routing (AB) + semantic intent classification tests."""

from __future__ import annotations

import pytest

from harness.core.agent_router import pick_version
from harness.core.semantic import SemanticIntentClassifier
from harness.models.intent import IntentCategory, IntentClassification


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows

    async def fetch_all(self, sql, params):
        return self.rows

    async def execute(self, sql, params):
        pass

    async def commit(self):
        pass


def _row(version, endpoint, weight):
    return {"version": version, "endpoint": endpoint, "weight": weight}


@pytest.mark.asyncio
async def test_pick_version_single_entry():
    db = _FakeDB([_row("1.0.0", "http://a:8110", 100)])
    assert (await pick_version(db, "log_analyst"))["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_pick_version_forced_pins_version():
    db = _FakeDB(
        [_row("1.0.0", "http://a:8110", 90), _row("2.0.0", "http://b:8110", 10)]
    )
    picked = await pick_version(db, "log_analyst", forced_version="2.0.0")
    assert picked["version"] == "2.0.0"


@pytest.mark.asyncio
async def test_pick_version_weighted_stays_in_range():
    db = _FakeDB(
        [_row("1.0.0", "http://a:8110", 99), _row("2.0.0", "http://b:8110", 1)]
    )
    versions = set()
    for _ in range(200):
        versions.add((await pick_version(db, "log_analyst"))["version"])
    # The 99% version dominates but both are reachable.
    assert "1.0.0" in versions


async def _embed_stub_factory():
    async def embed(text: str):
        # crude bag-of-words vector: index by known tokens
        vocab = ["log", "需求", "脚本", "用例", "执行", "error"]
        return [1.0 if tok in text else 0.0 for tok in vocab]

    return embed


@pytest.mark.asyncio
async def test_semantic_classifier_matches_nearest_capability():
    embed = await _embed_stub_factory()
    classifier = SemanticIntentClassifier(
        embed,
        keyword_map={
            IntentCategory.LOG_ANALYSIS: ["log", "error", "日志"],
            IntentCategory.SCRIPT_GENERATION: ["脚本", "script", "pytest"],
        },
        threshold=0.4,
    )
    result = await classifier.classify("帮我分析日志里的 error", {})
    assert result is not None
    assert result.intent == IntentCategory.LOG_ANALYSIS
    assert isinstance(result, IntentClassification)


@pytest.mark.asyncio
async def test_semantic_classifier_skips_without_embed():
    classifier = SemanticIntentClassifier(None, keyword_map={IntentCategory.GENERAL: ["x"]})
    assert await classifier.classify("anything", {}) is None

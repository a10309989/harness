"""Multi-version agent routing for AB / canary testing.

The control-plane ``agent_routing`` table maps an agent id to one or more
versions (each with an endpoint + traffic weight). The master picks a version
per request — pinned by ``forced_version`` (e.g. a request header) or weighted
random — so a new version can be canary'd at 10% and ramped without redeploy.
"""

from __future__ import annotations

import random

_SQL_ALL = (
    "SELECT version, endpoint, weight FROM agent_routing "
    "WHERE agent_id = $1 AND enabled = TRUE"
)
_SQL_UPSERT = (
    "INSERT INTO agent_routing (agent_id, version, endpoint, weight, enabled) "
    "VALUES ($1, $2, $3, $4, TRUE) "
    "ON CONFLICT (agent_id, version) DO UPDATE SET "
    "endpoint = $3, weight = $4, enabled = TRUE, updated_at = CURRENT_TIMESTAMP"
)


async def pick_version(db, agent_id: str, forced_version: str | None = None) -> dict | None:
    """Return the routing row for ``agent_id`` (weighted or forced).

    Returns None when the agent has no routing entries (caller uses the
    registry's default). Single-entry agents return that entry directly.
    """
    rows = await db.fetch_all(_SQL_ALL, (agent_id,))
    if not rows:
        return None
    if forced_version:
        for row in rows:
            if row["version"] == forced_version:
                return row
        return rows[0]
    if len(rows) == 1:
        return rows[0]
    total = sum(int(row["weight"] or 0) for row in rows)
    if total <= 0:
        return rows[0]
    pick = random.randint(1, total)
    acc = 0
    for row in rows:
        acc += int(row["weight"] or 0)
        if pick <= acc:
            return row
    return rows[0]


async def register_endpoint(
    db, agent_id: str, version: str, endpoint: str, weight: int = 100
) -> None:
    """Register (or update) a version entry for an agent."""
    await db.execute(_SQL_UPSERT, (agent_id, version, endpoint, weight))
    await db.commit()

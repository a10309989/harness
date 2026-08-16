"""ConfigStore — SQLite-based persistence for LLM and MCP configuration.

Provides a CRUD interface backed by SQLite, so users can configure LLM
providers and MCP servers through the UI, and changes survive server restarts.

Supports dynamic editing and loading — no restart needed for changes to take effect.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from harness.db.protocols import DatabaseProtocol

logger = logging.getLogger(__name__)


class ConfigStore:
    """Persistent config store backed by SQLite.

    Each config category (llm_providers, mcp_servers, etc.) maps to a table.
    All reads go directly to SQLite (fast enough), no caching needed.
    """

    def __init__(self, db: DatabaseProtocol) -> None:
        self.db = db

    # ─── Generic CRUD ──────────────────────────────────────────────

    async def get_all(self, category: str) -> list[dict[str, Any]]:
        """List all entries in a category (table)."""
        table = category
        rows = await self.db.fetch_all(f"SELECT * FROM {table}")
        return [self._row_to_dict(row) for row in rows]

    async def get(self, category: str, name: str) -> dict[str, Any] | None:
        """Get a single entry by name (primary key)."""
        table = category
        # For tables with 'name' as PK
        if table in ("llm_providers", "mcp_servers"):
            row = await self.db.fetch_one(
                f"SELECT * FROM {table} WHERE name = $1",
                (name,),
            )
        elif table == "agent_mcp_bindings":
            row = await self.db.fetch_one(
                f"SELECT * FROM {table} WHERE agent_id = $1",
                (name,),
            )
        elif table == "agent_llm_overrides":
            row = await self.db.fetch_one(
                f"SELECT * FROM {table} WHERE agent_id = $1",
                (name,),
            )
        elif table == "llm_settings":
            row = await self.db.fetch_one(
                f"SELECT * FROM {table} WHERE key = $1",
                (name,),
            )
        else:
            return None
        return self._row_to_dict(row) if row else None

    async def set(self, category: str, name: str, entry: dict[str, Any]) -> None:
        """Create or update an entry."""
        table = category
        now = self._now()

        if table == "llm_providers":
            await self._upsert_llm_provider(name, entry, now)
        elif table == "mcp_servers":
            await self._upsert_mcp_server(name, entry, now)
        elif table == "agent_mcp_bindings":
            await self._upsert_agent_mcp_bindings(name, entry)
        elif table == "agent_llm_overrides":
            await self._upsert_agent_llm_override(name, entry)
        elif table == "llm_settings":
            await self._upsert_llm_setting(name, entry)
        else:
            raise ValueError(f"Unknown config category: {table}")

    async def delete(self, category: str, name: str) -> bool:
        """Delete an entry. Returns True if it existed."""
        table = category
        if table in ("llm_providers", "mcp_servers"):
            cursor = await self.db.execute(
                f"DELETE FROM {table} WHERE name = $1",
                (name,),
            )
        elif table in ("agent_mcp_bindings", "agent_llm_overrides"):
            cursor = await self.db.execute(
                f"DELETE FROM {table} WHERE agent_id = $1",
                (name,),
            )
        elif table == "llm_settings":
            cursor = await self.db.execute(
                f"DELETE FROM {table} WHERE key = $1",
                (name,),
            )
        else:
            return False
        await self.db.commit()
        return cursor.rowcount > 0

    # ─── LLM Provider helpers ──────────────────────────────────────

    async def get_llm_providers(self) -> list[dict[str, Any]]:
        """List all LLM providers."""
        providers = await self.get_all("llm_providers")
        # Ensure 'provider' field exists for backward compatibility
        for p in providers:
            if "provider" not in p:
                p["provider"] = p.get("provider_label", "anthropic")
        return providers

    async def get_llm_provider(self, name: str) -> dict[str, Any] | None:
        """Get a single LLM provider by name."""
        return await self.get("llm_providers", name)

    async def save_llm_provider(self, name: str, config: dict[str, Any]) -> None:
        """Create or update an LLM provider."""
        await self.set("llm_providers", name, config)

    async def delete_llm_provider(self, name: str) -> bool:
        """Delete an LLM provider."""
        return await self.delete("llm_providers", name)

    # ─── MCP Server helpers ────────────────────────────────────────

    async def get_mcp_servers(self) -> list[dict[str, Any]]:
        """List all MCP servers."""
        servers = await self.get_all("mcp_servers")
        # Deserialize JSON fields
        for s in servers:
            if s.get("args"):
                s["args"] = self.db.from_json(s["args"])
            if s.get("env"):
                s["env"] = self.db.from_json(s["env"])
        return servers

    async def get_mcp_server(self, name: str) -> dict[str, Any] | None:
        """Get a single MCP server by name."""
        row = await self.get("mcp_servers", name)
        if row:
            if row.get("args"):
                row["args"] = self.db.from_json(row["args"])
            if row.get("env"):
                row["env"] = self.db.from_json(row["env"])
        return row

    async def save_mcp_server(self, name: str, config: dict[str, Any]) -> None:
        """Create or update an MCP server."""
        await self.set("mcp_servers", name, config)

    async def delete_mcp_server(self, name: str) -> bool:
        """Delete an MCP server."""
        return await self.delete("mcp_servers", name)

    # ─── Agent-MCP bindings ────────────────────────────────────────

    async def get_agent_mcp_bindings(self, agent_id: str) -> dict[str, Any]:
        """Get MCP bindings for a specific agent.

        Returns dict of {server_name: {tool_filter: [...]}}.
        """
        rows = await self.db.fetch_all(
            "SELECT server_name, tool_filter FROM agent_mcp_bindings WHERE agent_id = $1",
            (agent_id,),
        )
        result = {}
        for row in rows:
            server_name = row["server_name"]
            tool_filter = self.db.from_json(row["tool_filter"]) if row["tool_filter"] else None
            result[server_name] = {"tool_filter": tool_filter}
        return result

    async def set_agent_mcp_bindings(self, agent_id: str, bindings: dict[str, Any]) -> None:
        """Set MCP bindings for a specific agent.

        bindings format: {"server_name": {"tool_filter": ["tool_a", "tool_b"]}, ...}
        """
        # Replace the agent's bindings atomically: a crash or concurrent request
        # between the DELETE and the INSERTs must not leave a partial/empty set.
        async with self.db.transaction() as conn:
            await conn.execute(
                "DELETE FROM agent_mcp_bindings WHERE agent_id = $1",
                (agent_id,),
            )
            for server_name, config in bindings.items():
                tool_filter = config.get("tool_filter")
                await conn.execute(
                    "INSERT INTO agent_mcp_bindings (agent_id, server_name, tool_filter) VALUES ($1, $2, $3)",
                    (agent_id, server_name, self.db.to_json(tool_filter)),
                )

    # ─── Cache management ──────────────────────────────────────────

    async def invalidate_cache(self) -> None:
        """No-op for SQLite (no caching). Kept for API compatibility."""
        pass

    # ─── Internal helpers ──────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row) -> dict[str, Any]:
        """Convert a DB row to a plain dict."""
        return dict(row)

    async def _upsert_llm_provider(self, name: str, entry: dict, now) -> None:
        await self.db.execute(
            """INSERT INTO llm_providers (name, provider, api_key, model, base_url, provider_label, temperature, max_tokens, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                   ON CONFLICT(name) DO UPDATE SET
                       provider=excluded.provider, api_key=excluded.api_key, model=excluded.model,
                       base_url=excluded.base_url, provider_label=excluded.provider_label,
                       temperature=excluded.temperature, max_tokens=excluded.max_tokens, updated_at=excluded.updated_at""",
            (
                name,
                entry.get("provider", "anthropic"),
                entry.get("api_key", ""),
                entry.get("model", ""),
                entry.get("base_url", ""),
                entry.get("provider_label", entry.get("provider", "anthropic")),
                entry.get("temperature", 0.2),
                entry.get("max_tokens", 4096),
                now,
            ),
        )
        await self.db.commit()

    async def _upsert_mcp_server(self, name: str, entry: dict, now) -> None:
        args_json = self.db.to_json(entry.get("args"))
        env_json = self.db.to_json(entry.get("env"))
        await self.db.execute(
            """INSERT INTO mcp_servers (name, command, args, env, enabled, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6)
                   ON CONFLICT(name) DO UPDATE SET
                       command=excluded.command, args=excluded.args, env=excluded.env,
                       enabled=excluded.enabled, updated_at=excluded.updated_at""",
            (
                name,
                entry.get("command", ""),
                args_json,
                env_json,
                1 if entry.get("enabled", True) else 0,
                now,
            ),
        )
        await self.db.commit()

    async def _upsert_agent_mcp_bindings(self, agent_id: str, entry: dict) -> None:
        # entry is the bindings dict: {server_name: {tool_filter: [...]}}
        async with self.db.transaction() as conn:
            await conn.execute(
                "DELETE FROM agent_mcp_bindings WHERE agent_id = $1",
                (agent_id,),
            )
            for server_name, config in entry.items():
                tool_filter = config.get("tool_filter") if isinstance(config, dict) else None
                await conn.execute(
                    "INSERT INTO agent_mcp_bindings (agent_id, server_name, tool_filter) VALUES ($1, $2, $3)",
                    (agent_id, server_name, self.db.to_json(tool_filter)),
                )

    async def _upsert_agent_llm_override(self, agent_id: str, entry: dict) -> None:
        await self.db.execute(
            """INSERT INTO agent_llm_overrides (agent_id, provider, model, api_key, base_url)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT(agent_id) DO UPDATE SET
                       provider=excluded.provider, model=excluded.model,
                       api_key=excluded.api_key, base_url=excluded.base_url""",
            (
                agent_id,
                entry.get("provider", ""),
                entry.get("model", ""),
                entry.get("api_key_name", entry.get("api_key", "")),
                entry.get("base_url", ""),
            ),
        )
        await self.db.commit()

    async def _upsert_llm_setting(self, key: str, entry: dict) -> None:
        value = entry.get("value", "") if isinstance(entry, dict) else str(entry)
        await self.db.execute(
            """INSERT INTO llm_settings (key, value) VALUES ($1, $2)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (key, value),
        )
        await self.db.commit()

    def _now(self):
        return datetime.now(timezone.utc).replace(tzinfo=None)

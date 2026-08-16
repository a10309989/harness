"""Authentication service with development and API-key providers."""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timezone

from harness.db.protocols import DatabaseProtocol
from harness.observability.context import new_id
from harness.security.models import ActorContext, ActorType
from harness.security.permissions import PERMISSIONS, ROLE_PERMISSIONS


class AuthenticationError(Exception):
    pass


class AuthorizationError(Exception):
    pass


class AuthService:
    def __init__(self, db: DatabaseProtocol, mode: str | None = None) -> None:
        self.db = db
        self.mode = (mode or os.environ.get("HARNESS_SECURITY_MODE", "dev")).lower()

    async def initialize(self) -> None:
        await self._seed_rbac()
        await self._ensure_actor(
            actor_id="default-user",
            actor_type=ActorType.USER,
            display_name="Development Administrator",
            roles=("admin",),
        )
        await self._ensure_actor(
            actor_id="system",
            actor_type=ActorType.SYSTEM,
            display_name="Harness System",
            roles=("admin",),
        )
        bootstrap_key = os.environ.get("HARNESS_BOOTSTRAP_API_KEY", "")
        if bootstrap_key:
            await self._store_credential("default-user", bootstrap_key, credential_id="bootstrap")

    async def authenticate(self, authorization: str | None) -> ActorContext:
        if authorization and authorization.lower().startswith("bearer "):
            api_key = authorization.split(" ", 1)[1].strip()
            actor = await self._authenticate_api_key(api_key)
            if actor is not None:
                return actor
            raise AuthenticationError("Invalid or expired API key")

        if self.mode == "dev":
            return await self.get_actor("default-user", auth_method="dev")
        raise AuthenticationError("Bearer API key is required")

    async def get_actor(self, actor_id: str, auth_method: str = "internal") -> ActorContext:
        row = await self.db.fetch_one(
            "SELECT * FROM actors WHERE id = $1 AND enabled = 1",
            (actor_id,),
        )
        if not row:
            raise AuthenticationError(f"Actor '{actor_id}' not found or disabled")
        role_rows = await self.db.fetch_all(
            "SELECT role_name FROM actor_roles WHERE actor_id = $1 ORDER BY role_name",
            (actor_id,),
        )
        roles = tuple(row["role_name"] for row in role_rows)
        permission_rows = await self.db.fetch_all(
            """SELECT DISTINCT rp.permission_name
               FROM role_permissions rp
               JOIN actor_roles ar ON ar.role_name = rp.role_name
               WHERE ar.actor_id = $1""",
            (actor_id,),
        )
        permissions = frozenset(row["permission_name"] for row in permission_rows)
        return ActorContext(
            actor_id=row["id"],
            actor_type=ActorType(row["actor_type"]),
            tenant_id=row["tenant_id"],
            display_name=row["display_name"],
            roles=roles,
            permissions=permissions,
            auth_method=auth_method,
        )

    async def create_api_key(self, actor_id: str) -> tuple[str, dict]:
        await self.get_actor(actor_id)
        raw_key = f"harness_{secrets.token_urlsafe(32)}"
        credential_id = new_id()
        await self._store_credential(actor_id, raw_key, credential_id=credential_id)
        return raw_key, {
            "id": credential_id,
            "actor_id": actor_id,
            "key_prefix": raw_key[:12],
        }

    async def create_actor(
        self,
        *,
        actor_id: str,
        actor_type: ActorType,
        display_name: str,
        tenant_id: str = "default",
        roles: tuple[str, ...] = (),
    ) -> ActorContext:
        now = self._now()
        async with self.db.transaction() as conn:
            await conn.execute(
                """INSERT INTO actors
                       (id, actor_type, tenant_id, display_name, enabled, created_at, updated_at)
                       VALUES ($1, $2, $3, $4, 1, $5, $6)""",
                (actor_id, actor_type.value, tenant_id, display_name, now, now),
            )
            for role in roles:
                await conn.execute(
                    "INSERT INTO actor_roles (actor_id, role_name) VALUES ($1, $2)",
                    (actor_id, role),
                )
        return await self.get_actor(actor_id)

    async def set_actor_roles(
        self,
        actor_id: str,
        roles: tuple[str, ...],
    ) -> ActorContext:
        await self.get_actor(actor_id)
        async with self.db.transaction() as conn:
            await conn.execute(
                "DELETE FROM actor_roles WHERE actor_id = $1",
                (actor_id,),
            )
            for role in roles:
                await conn.execute(
                    "INSERT INTO actor_roles (actor_id, role_name) VALUES ($1, $2)",
                    (actor_id, role),
                )
        return await self.get_actor(actor_id)

    async def list_actors(self) -> list[dict]:
        rows = await self.db.fetch_all(
            "SELECT * FROM actors WHERE enabled = 1 ORDER BY created_at, id"
        )
        result = []
        for row in rows:
            actor = await self.get_actor(row["id"])
            result.append(actor.to_dict())
        return result

    async def revoke_api_key(self, credential_id: str) -> bool:
        cursor = await self.db.execute(
            "UPDATE api_credentials SET enabled = 0 WHERE id = $1 AND enabled = 1",
            (credential_id,),
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def _authenticate_api_key(self, api_key: str) -> ActorContext | None:
        key_hash = self._hash_key(api_key)
        now = self._now()
        row = await self.db.fetch_one(
            """SELECT actor_id FROM api_credentials
                   WHERE key_hash = $1 AND enabled = 1
                   AND (expires_at IS NULL OR expires_at > $2)""",
            (key_hash, now),
        )
        if not row:
            return None
        await self.db.execute(
            "UPDATE api_credentials SET last_used_at = $1 WHERE key_hash = $2",
            (now, key_hash),
        )
        await self.db.commit()
        return await self.get_actor(row["actor_id"], auth_method="api_key")

    async def _seed_rbac(self) -> None:
        async with self.db.transaction() as conn:
            for permission, description in PERMISSIONS.items():
                await conn.execute(
                    "INSERT INTO permissions (name, description) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    (permission, description),
                )
            for role, permissions in ROLE_PERMISSIONS.items():
                await conn.execute(
                    "INSERT INTO roles (name, description) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    (role, f"Built-in {role} role"),
                )
                for permission in permissions:
                    await conn.execute(
                        "INSERT INTO role_permissions (role_name, permission_name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                        (role, permission),
                    )

    async def _ensure_actor(
        self,
        *,
        actor_id: str,
        actor_type: ActorType,
        display_name: str,
        roles: tuple[str, ...],
    ) -> None:
        now = self._now()
        async with self.db.transaction() as conn:
            await conn.execute(
                """INSERT INTO actors (id, actor_type, tenant_id, display_name, enabled, created_at, updated_at)
                       VALUES ($1, $2, 'default', $3, 1, $4, $5)
                       ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name, updated_at=excluded.updated_at""",
                (actor_id, actor_type.value, display_name, now, now),
            )
            for role in roles:
                await conn.execute(
                    "INSERT INTO actor_roles (actor_id, role_name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    (actor_id, role),
                )

    async def _store_credential(
        self,
        actor_id: str,
        api_key: str,
        *,
        credential_id: str,
    ) -> None:
        await self.db.execute(
            """INSERT INTO api_credentials
                   (id, actor_id, key_hash, key_prefix, enabled)
                   VALUES ($1, $2, $3, $4, 1) ON CONFLICT DO NOTHING""",
            (credential_id, actor_id, self._hash_key(api_key), api_key[:12]),
        )
        await self.db.commit()

    def _now(self):
        return datetime.now(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _hash_key(api_key: str) -> str:
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

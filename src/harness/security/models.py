"""Security identity models."""

from dataclasses import dataclass, field
from enum import StrEnum


class ActorType(StrEnum):
    USER = "user"
    SERVICE = "service"
    AGENT = "agent"
    SYSTEM = "system"


@dataclass(frozen=True)
class ActorContext:
    """Authenticated principal propagated through one execution."""

    actor_id: str
    actor_type: ActorType = ActorType.USER
    tenant_id: str = "default"
    display_name: str = ""
    roles: tuple[str, ...] = ()
    permissions: frozenset[str] = field(default_factory=frozenset)
    auth_method: str = "dev"

    def has_permission(self, permission: str) -> bool:
        return "*" in self.permissions or permission in self.permissions

    def to_dict(self) -> dict:
        return {
            "actor_id": self.actor_id,
            "actor_type": self.actor_type.value,
            "tenant_id": self.tenant_id,
            "display_name": self.display_name,
            "roles": list(self.roles),
            "permissions": sorted(self.permissions),
            "auth_method": self.auth_method,
        }

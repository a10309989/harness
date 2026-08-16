"""ULID-based ID generation."""

from ulid import ULID


def generate_id() -> str:
    """Generate a ULID-based unique identifier."""
    return str(ULID())


def generate_short_id() -> str:
    """Generate a short ID (first 8 chars of ULID)."""
    return str(ULID())[:8]

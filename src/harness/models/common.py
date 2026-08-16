"""Base model with common configuration."""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HarnessBaseModel(BaseModel):
    """Base model with common configuration for all Harness models."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        use_enum_values=True,
        json_encoders={datetime: lambda v: v.isoformat()},
    )


class TimestampedModel(HarnessBaseModel):
    """Mixin that adds created_at / updated_at timestamps."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ErrorResponse(HarnessBaseModel):
    """Standard error response model."""

    error_code: str
    message: str
    details: dict[str, Any] | None = None
    session_id: str | None = None


class Pagination(HarnessBaseModel):
    """Pagination parameters."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    total: int = 0

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

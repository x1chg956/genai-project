"""Shared field types and the hand-off base model (docs/03 §3, docs/12)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field

CENT = Decimal("0.01")


def _reject_float(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError("money must be a Decimal or a decimal string, never a float")
    return value


def _to_cents(value: Decimal) -> Decimal:
    # Inputs already have at most 2 places (enforced by the Field), so this never rounds;
    # it only normalises Decimal("10.0") to Decimal("10.00").
    return value.quantize(CENT)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


Money = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    Field(max_digits=18, decimal_places=2),
    AfterValidator(_to_cents),
]
"""INR amount, exact to the paisa. Serialised to JSON as a decimal string."""

UtcDatetime = Annotated[datetime, AfterValidator(_require_utc)]
"""Timezone-aware timestamp, normalised to UTC."""

Ratio = Annotated[Decimal, BeforeValidator(_reject_float)]
"""Exact decimal ratio (shares, coverage), never a float."""


class Model(BaseModel):
    """Base for every schema: unknown fields are rejected, instances are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


ErrorCode = Literal[
    "INVALID_INPUT",
    "NOT_FOUND",
    "FORBIDDEN",
    "OUT_OF_SCOPE",
    "STALE_HASH",
    "APPROVAL_INVALID",
    "TIMEOUT",
    "BUDGET_EXCEEDED",
    "INTERNAL",
]


class ToolError(Model):
    """Structured error returned (never raised) by MCP tools and code nodes."""

    error_code: ErrorCode
    message: str
    retryable: bool
    tool_name: str
    correlation_id: str


class HandOff(Model):
    """Every hand-off between nodes carries identity, status, evidence refs and error info."""

    case_id: str
    run_id: str
    schema_version: str = "1"
    status: Literal["ok", "partial", "error"] = "ok"
    evidence_refs: list[str] = Field(default_factory=list)
    error: ToolError | None = None

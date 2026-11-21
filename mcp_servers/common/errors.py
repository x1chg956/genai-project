"""Structured tool errors (docs/05 Conventions). Tools return these; they never raise."""

from __future__ import annotations

import uuid

from auditguard.schemas import ToolError
from auditguard.schemas.common import ErrorCode

_RETRYABLE: frozenset[str] = frozenset({"TIMEOUT"})


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def tool_error(
    code: ErrorCode,
    message: str,
    tool_name: str,
    *,
    correlation_id: str | None = None,
    retryable: bool | None = None,
) -> ToolError:
    return ToolError(
        error_code=code,
        message=message,
        retryable=code in _RETRYABLE if retryable is None else retryable,
        tool_name=tool_name,
        correlation_id=correlation_id or new_correlation_id(),
    )

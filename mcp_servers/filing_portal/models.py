"""Tool outputs of the filing_portal server."""

from __future__ import annotations

from pydantic import Field

from auditguard.schemas import Model

_SHA256 = r"^[0-9a-f]{64}$"


class PayloadPreview(Model):
    case_id: str
    report_version: int = Field(ge=1)
    canonical_json: str
    sha256: str = Field(pattern=_SHA256)
    form_limit_ok: bool
    form_limit_issues: list[str] = Field(default_factory=list)

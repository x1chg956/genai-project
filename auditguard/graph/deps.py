"""Dependencies passed into graph nodes (docs/12: no global state)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable

from auditguard.approval.records import ApprovalRepo
from auditguard.config import Settings
from auditguard.guardrails.output import OutputPolicy
from auditguard.guardrails.pseudonymise import identity
from auditguard.hooks.post_tool import AuditSink, NullAuditSink
from auditguard.hooks.tools import ClientFactory
from auditguard.prompts import Prompt


@dataclass(frozen=True)
class GraphDeps:
    settings: Settings
    clients: ClientFactory
    approvals: ApprovalRepo
    reason_codes: dict[str, dict[str, str]]
    chat_model: BaseChatModel
    answer_model: Runnable[Any, Any]
    prompt: Prompt
    clock: Callable[[], datetime]
    audit: AuditSink = field(default_factory=NullAuditSink)
    shown: Callable[[str], str] = identity
    """Applied to every text a model is shown: the pseudonymiser (docs/08 §3)."""
    output_policy: OutputPolicy | None = None

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.settings.timezone)

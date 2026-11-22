"""The configured approval store (`approval.backend`): Postgres from Phase 2, or SQLite."""

from __future__ import annotations

from auditguard.approval.records import ApprovalRepo, ApprovalStore, PostgresApprovalStore
from auditguard.config import Settings, resolve_path
from auditguard.db import conninfo


def approval_store(settings: Settings, *, read_only: bool = False) -> ApprovalRepo:
    cfg = settings.approval
    if cfg.backend == "postgres":
        app = conninfo(settings.databases.app, application_name="approvals")
        return PostgresApprovalStore(app, cfg.schema_, read_only=read_only)
    return ApprovalStore(resolve_path(cfg.store_path), read_only=read_only)

"""Tracing for every model call (docs/11 §7).

The backend comes from `tracing` in config. `auditguard/llm.py` attaches `tracing_callbacks`
to every client it builds; graph runs pass `trace_config(...)` so one run is one trace whose
root run ID is the trace ID stored in the run record. A live run (run-case, eval, llm-check)
calls `require_live_tracing` first and refuses backend "none".

One tracer is cached per (endpoint, project, key digest). Reusing the same handler object lets
LangChain de-duplicate it when it is attached both to a model and to a graph run's config, so
model calls nest under the run's trace instead of being reported twice.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig
from langchain_core.tracers.langchain import LangChainTracer, wait_for_all_tracers
from langsmith import Client

from auditguard.config import ConfigError, Settings, require_secret

_TRACERS: dict[tuple[str, str, str, str], LangChainTracer] = {}


def _langsmith_tracer(settings: Settings) -> LangChainTracer:
    cfg = settings.tracing
    key = require_secret(cfg.api_key, "LANGSMITH_API_KEY")
    cache_key = (cfg.endpoint, cfg.project, cfg.workspace_id or "", hashlib.sha256(key).hexdigest())
    tracer = _TRACERS.get(cache_key)
    if tracer is None:
        client = Client(
            api_url=cfg.endpoint, api_key=key.decode("utf-8"), workspace_id=cfg.workspace_id
        )
        tracer = LangChainTracer(project_name=cfg.project, client=client)
        _TRACERS[cache_key] = tracer
    return tracer


def tracing_callbacks(settings: Settings) -> list[BaseCallbackHandler]:
    """The configured tracer as a callback list; empty for backend "none"."""
    backend = settings.tracing.backend
    if backend == "none":
        return []
    if backend == "langfuse":
        raise ConfigError(
            "tracing.backend 'langfuse' is not wired yet (Phase 7, Compose profile langfuse); "
            "set TRACING_BACKEND=langsmith"
        )
    return [_langsmith_tracer(settings)]


def require_live_tracing(settings: Settings) -> None:
    """Refuse a live run without tracing: a run without a trace ID fails (docs/14)."""
    if settings.tracing.backend == "none":
        raise ConfigError(
            "live runs need tracing; tracing.backend is 'none' (TRACING_BACKEND). "
            "Use 'none' for offline unit tests only"
        )
    tracing_callbacks(settings)


def trace_config(
    settings: Settings,
    *,
    run_id: UUID,
    run_name: str,
    metadata: Mapping[str, str] | None = None,
) -> RunnableConfig:
    """Config for one traced run: its root run ID is the trace ID.

    Metadata always carries the config hash; callers add case_id, run_id, the experiment arm
    and model IDs. Values must be pseudonymised identifiers, never raw customer data.
    """
    return RunnableConfig(
        run_id=run_id,
        run_name=run_name,
        metadata={**(metadata or {}), "config_hash": settings.config_hash},
        callbacks=tracing_callbacks(settings),
    )


def flush_traces() -> None:
    """Block until every queued trace has been sent."""
    wait_for_all_tracers()
    for tracer in _TRACERS.values():
        tracer.client.flush()


def confirm_trace(settings: Settings, trace_id: UUID, *, timeout_s: float = 20.0) -> bool:
    """True once the backend returns the root run `trace_id` (polls; ingestion is async)."""
    tracer = _langsmith_tracer(settings)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            tracer.client.read_run(trace_id)
            return True
        except Exception:  # not ingested yet, or the backend refused the read
            if time.monotonic() >= deadline:
                return False
            time.sleep(1.0)

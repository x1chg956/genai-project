"""Shared fixtures. Tests never read the real .env: they pass an explicit environment."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from auditguard.config import Settings, load_settings

TEST_ENV = {
    "LLM_API_KEY": "test-local-key",
    "OPENROUTER_API_KEY": "test-openrouter-key",
    "POSTGRES_PASSWORD": "test-app-pw",
    "GROUNDTRUTH_PASSWORD": "test-gt-pw",
    "LEDGER_RO_PASSWORD": "test-ro-pw",
    "MCP_TOKEN_KEY": "test-mcp-token-key",
    "APPROVAL_HMAC_KEY": "test-approval-hmac-key",
    "TRACING_BACKEND": "none",
    "CHECKPOINTER": "sqlite",  # unit tests never touch a real database
    "APPROVAL_BACKEND": "sqlite",
}


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    def _make(**env: str) -> Settings:
        return load_settings(environ={**TEST_ENV, **env})

    return _make


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings()


@pytest.fixture(autouse=True)
def _no_ambient_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests never send traces: switch off LangChain's environment-driven tracing."""
    for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
        monkeypatch.setenv(name, "false")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)

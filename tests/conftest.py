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
}


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    def _make(**env: str) -> Settings:
        return load_settings(environ={**TEST_ENV, **env})

    return _make


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings()

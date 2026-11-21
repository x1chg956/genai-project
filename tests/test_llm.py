from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from langchain_openai import ChatOpenAI, OpenAI

from auditguard.config import REPO_ROOT, ConfigError, Settings
from auditguard.llm import get_guard, get_model


def test_llm_factory_uses_config(settings: Settings) -> None:
    model = get_model("specialist", settings)
    assert isinstance(model, ChatOpenAI)
    assert model.openai_api_base == settings.providers["local"].base_url
    assert model.model_name == settings.models["specialist"].id
    assert model.temperature == settings.models["specialist"].temperature
    assert model.max_tokens == settings.models["specialist"].max_tokens
    assert model.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_stop_sequences_come_from_config(settings: Settings) -> None:
    judge = get_model("judge", settings)
    specialist = get_model("specialist", settings)
    assert isinstance(judge, ChatOpenAI) and isinstance(specialist, ChatOpenAI)
    assert judge.stop == ["<end_of_turn>"]
    assert specialist.stop is None


def test_env_override(make_settings: Callable[..., Settings]) -> None:
    model = get_model("specialist", make_settings(LLM_BASE_URL="http://elsewhere:9999/v1"))
    assert isinstance(model, ChatOpenAI)
    assert model.openai_api_base == "http://elsewhere:9999/v1"


def test_role_provider_switch(make_settings: Callable[..., Settings]) -> None:
    local = get_model("judge", make_settings())
    hosted = get_model(
        "judge", make_settings(JUDGE_PROVIDER="openrouter", JUDGE_MODEL="google/gemini-x")
    )
    assert isinstance(local, ChatOpenAI) and isinstance(hosted, ChatOpenAI)
    assert local.openai_api_base == "http://127.0.0.1:8888/v1"
    assert hosted.openai_api_base == "https://openrouter.ai/api/v1"
    assert local.openai_api_key is not None and hosted.openai_api_key is not None
    assert local.openai_api_key.get_secret_value() == "test-local-key"
    assert hosted.openai_api_key.get_secret_value() == "test-openrouter-key"


def test_hosted_provider_sends_no_local_extra_body(make_settings: Callable[..., Settings]) -> None:
    hosted = get_model("judge", make_settings(JUDGE_PROVIDER="openrouter", JUDGE_MODEL="g/x"))
    assert isinstance(hosted, ChatOpenAI)
    assert hosted.extra_body is None


def test_missing_api_key_fails_clearly(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ConfigError, match="api_key is empty"):
        get_model("specialist", make_settings(LLM_API_KEY=""))


def test_unknown_role_fails_clearly(settings: Settings) -> None:
    with pytest.raises(ConfigError, match="unknown model role"):
        get_model("oracle", settings)


def test_unpinned_fallback_fails_clearly(settings: Settings) -> None:
    with pytest.raises(ConfigError, match="disabled"):
        get_model("fallback", settings)


def test_guard_raw_mode_uses_completions_client(settings: Settings) -> None:
    guard = get_guard(settings)
    assert isinstance(guard, OpenAI)
    assert guard.model_name == "Llama-Guard-3-1B-chat-mlx-4bit"
    with pytest.raises(ConfigError, match="get_guard"):
        get_model("guard", settings)


def test_guard_chat_mode_uses_chat_client(make_settings: Callable[..., Settings]) -> None:
    guard = get_guard(make_settings(GUARD_PROVIDER="openrouter", GUARD_MODE="chat"))
    assert isinstance(guard, ChatOpenAI)


def test_no_creation_makes_a_network_call(settings: Settings) -> None:
    # Building a client must not contact the server: this runs with no server available.
    get_model("supervisor", settings)
    get_guard(settings)


def test_no_hard_coded_endpoints_or_models_in_package() -> None:
    pattern = re.compile(r"127\.0\.0\.1|localhost:\d|openrouter\.ai|Qwen3|gemma-|Llama-Guard")
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{n}"
        for path in Path(REPO_ROOT, "auditguard").rglob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []

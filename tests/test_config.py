from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from auditguard.config import (
    DEFAULT_CONFIG,
    Settings,
    build_environ,
    deep_merge,
    expand_vars,
    load_settings,
)


def test_expand_default_used_when_unset() -> None:
    assert expand_vars("${X:-fallback}", {}) == "fallback"


def test_expand_default_used_when_empty() -> None:
    assert expand_vars("${X:-fallback}", {"X": ""}) == "fallback"


def test_expand_env_value_wins() -> None:
    assert expand_vars("${X:-fallback}", {"X": "set"}) == "set"


def test_expand_unset_without_default_is_empty() -> None:
    assert expand_vars("${X}", {}) == ""


def test_expand_embedded_and_nested() -> None:
    tree = {"a": ["pre-${A}-post", {"b": "${B:-2}"}], "n": 5}
    assert expand_vars(tree, {"A": "x"}) == {"a": ["pre-x-post", {"b": "2"}], "n": 5}


def test_expand_cannot_inject_yaml_structure() -> None:
    value = expand_vars("${X}", {"X": "a: b\nc: [1, 2]"})
    assert value == "a: b\nc: [1, 2]"  # stays one string; never re-parsed as YAML


def test_base_config_loads_with_spec_defaults(settings: Settings) -> None:
    assert settings.providers["local"].base_url == "http://127.0.0.1:8888/v1"
    assert settings.models["specialist"].id == "Qwen3.5-4B-4bit"
    assert settings.models["judge"].provider == "local"
    assert settings.models["judge"].id == "gemma-3-1b-it-4bit"
    assert settings.models["guard"].mode == "completions_raw"
    assert settings.timezone == "Asia/Kolkata"
    assert settings.graph == "c1"


def test_budget_money_is_exact_decimal(settings: Settings) -> None:
    assert settings.budgets.case.cost_usd == Decimal("0.50")
    assert isinstance(settings.budgets.case.cost_usd, Decimal)
    rates = settings.providers["local"].cost_per_1k_tokens
    assert rates is not None
    assert rates.input == Decimal("0")


def test_later_phase_fields_are_optional(settings: Settings) -> None:
    assert settings.models["fallback"].id is None
    assert settings.corpus.version is None
    assert settings.mcp.servers == {}


def test_database_port_is_int_and_overridable(make_settings: Callable[..., Settings]) -> None:
    assert make_settings().databases.app.port == 5432
    assert make_settings(POSTGRES_PORT="5433").databases.app.port == 5433


def test_unset_secret_is_none(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(LLM_API_KEY="")
    assert settings.providers["local"].api_key is None


def test_secrets_never_in_repr_or_dump(settings: Settings) -> None:
    text = repr(settings) + str(settings) + settings.model_dump_json()
    for secret in ("test-local-key", "test-openrouter-key", "test-app-pw", "test-gt-pw"):
        assert secret not in text


def test_config_hash_is_stable(make_settings: Callable[..., Settings]) -> None:
    assert make_settings().config_hash == make_settings().config_hash


def test_config_hash_ignores_secrets(make_settings: Callable[..., Settings]) -> None:
    assert make_settings().config_hash == make_settings(LLM_API_KEY="rotated").config_hash


def test_config_hash_changes_with_values(make_settings: Callable[..., Settings]) -> None:
    assert make_settings().config_hash != make_settings(LLM_MODEL="other-model").config_hash


def test_structured_method_falls_back_to_provider(settings: Settings) -> None:
    assert settings.models["specialist"].structured_method is None
    assert settings.structured_method_for("specialist") == "json_schema"


def test_structured_method_role_overrides_provider(tmp_path: Path) -> None:
    override = tmp_path / "exp.yaml"
    override.write_text("models:\n  judge: {structured_method: function_calling}\n")
    settings = load_settings(DEFAULT_CONFIG, override, environ={})
    assert settings.structured_method_for("judge") == "function_calling"
    assert settings.structured_method_for("specialist") == "json_schema"


def test_experiment_override_deep_merges(tmp_path: Path) -> None:
    override = tmp_path / "exp.yaml"
    override.write_text("graph: c0\nretrieval: {rerank: false}\n")
    settings = load_settings(DEFAULT_CONFIG, override, environ={})
    assert settings.graph == "c0"
    assert settings.retrieval.rerank is False
    assert settings.retrieval.bm25 is True  # untouched sibling kept


def test_unknown_key_rejected(tmp_path: Path) -> None:
    override = tmp_path / "typo.yaml"
    override.write_text("budgets: {case: {tool_cals: 5}}\n")
    with pytest.raises(ValidationError):
        load_settings(DEFAULT_CONFIG, override, environ={})


def test_deep_merge_does_not_mutate_inputs() -> None:
    base = {"a": {"b": 1, "c": 2}}
    merged = deep_merge(base, {"a": {"b": 9}})
    assert merged == {"a": {"b": 9, "c": 2}}
    assert base == {"a": {"b": 1, "c": 2}}


def test_dotenv_fills_unset_but_never_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_BASE_URL=http://from-dotenv/v1\nLLM_MODEL=dotenv-model\n")
    monkeypatch.setenv("LLM_MODEL", "process-model")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    environ = build_environ(env_file)
    assert environ["LLM_BASE_URL"] == "http://from-dotenv/v1"
    assert environ["LLM_MODEL"] == "process-model"

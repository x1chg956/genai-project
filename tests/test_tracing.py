from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.tracers.context import collect_runs
from langchain_core.tracers.langchain import LangChainTracer
from langchain_openai import ChatOpenAI

from auditguard.config import ConfigError, Settings
from auditguard.llm import get_guard, get_model
from auditguard.tracing import require_live_tracing, trace_config, tracing_callbacks
from scripts import llm_check

LANGSMITH = {"TRACING_BACKEND": "langsmith", "LANGSMITH_API_KEY": "test-langsmith-key"}


def test_default_backend_is_langsmith(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(TRACING_BACKEND="")
    assert settings.tracing.backend == "langsmith"
    assert settings.tracing.project == "auditguard"


def test_none_backend_attaches_no_callbacks(settings: Settings) -> None:
    assert tracing_callbacks(settings) == []
    model = get_model("specialist", settings)
    assert isinstance(model, ChatOpenAI)
    assert model.callbacks is None


def test_langsmith_tracer_attached_to_every_client(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(**LANGSMITH, TRACING_PROJECT="ag-test")
    chat = get_model("specialist", settings)
    guard = get_guard(settings)
    for client in (chat, guard):
        assert isinstance(client.callbacks, list) and len(client.callbacks) == 1
        tracer = client.callbacks[0]
        assert isinstance(tracer, LangChainTracer)
        assert tracer.project_name == "ag-test"


def test_tracer_is_shared_so_langchain_can_deduplicate(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(**LANGSMITH)
    assert tracing_callbacks(settings)[0] is tracing_callbacks(settings)[0]


def test_missing_key_names_the_variable_not_a_value(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(TRACING_BACKEND="langsmith", LANGSMITH_API_KEY="")
    with pytest.raises(ConfigError, match="LANGSMITH_API_KEY is not set"):
        get_model("specialist", settings)


def test_key_is_not_in_the_config_hash_or_repr(make_settings: Callable[..., Settings]) -> None:
    a = make_settings(**LANGSMITH)
    b = make_settings(**{**LANGSMITH, "LANGSMITH_API_KEY": "rotated-key"})
    assert a.config_hash == b.config_hash
    assert "test-langsmith-key" not in repr(a.tracing)


def test_live_runs_refuse_backend_none(settings: Settings) -> None:
    with pytest.raises(ConfigError, match="live runs need tracing"):
        require_live_tracing(settings)


def test_live_runs_accept_a_configured_backend(make_settings: Callable[..., Settings]) -> None:
    require_live_tracing(make_settings(**LANGSMITH))


def test_langfuse_is_refused_until_wired(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ConfigError, match="langfuse"):
        require_live_tracing(make_settings(TRACING_BACKEND="langfuse"))


def test_trace_config_carries_run_id_and_config_hash(settings: Settings) -> None:
    run_id = uuid4()
    config = trace_config(
        settings, run_id=run_id, run_name="case", metadata={"case_id": "ALR-SYN-0001"}
    )
    assert config["run_id"] == run_id
    assert config["run_name"] == "case"
    assert config["metadata"] == {"case_id": "ALR-SYN-0001", "config_hash": settings.config_hash}


def test_root_run_id_is_the_trace_id(settings: Settings) -> None:
    """The run record stores the run_id we pass; the backend's root run gets exactly that ID."""
    run_id = uuid4()
    model = FakeListChatModel(responses=["OK"])
    with collect_runs() as collector:
        model.invoke(
            "Reply with exactly OK", config=trace_config(settings, run_id=run_id, run_name="t")
        )
    assert [run.id for run in collector.traced_runs] == [run_id]
    assert collector.traced_runs[0].extra["metadata"]["config_hash"] == settings.config_hash


def _rows(chat_status: str) -> list[tuple[str, str, str, llm_check.ModelReport, set[str]]]:
    report = llm_check.ModelReport({"chat": llm_check.Check(chat_status)})  # type: ignore[arg-type]
    return [("specialist", "local", "m", report, set())]


def test_llm_check_trace_reads_back_the_trace_it_sent(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[object] = []
    monkeypatch.setattr(llm_check, "get_model", lambda role, s: FakeListChatModel(responses=["OK"]))
    monkeypatch.setattr(llm_check, "flush_traces", lambda: None)
    monkeypatch.setattr(
        llm_check, "confirm_trace", lambda s, trace_id: sent.append(trace_id) or True
    )
    check = llm_check.check_trace(_rows("PASS"), settings)
    assert check.status == "PASS"
    assert f"trace_id={sent[0]}" in check.detail


def test_llm_check_trace_fails_when_backend_never_shows_it(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_check, "get_model", lambda role, s: FakeListChatModel(responses=["OK"]))
    monkeypatch.setattr(llm_check, "flush_traces", lambda: None)
    monkeypatch.setattr(llm_check, "confirm_trace", lambda s, trace_id: False)
    assert llm_check.check_trace(_rows("PASS"), settings).status == "FAIL"


def test_llm_check_trace_needs_a_working_core_role(settings: Settings) -> None:
    assert llm_check.check_trace(_rows("FAIL"), settings).status == "FAIL"


def test_workspace_id_reaches_the_client(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(**LANGSMITH, LANGSMITH_WORKSPACE_ID="ws-test-0001")
    tracer = tracing_callbacks(settings)[0]
    assert isinstance(tracer, LangChainTracer)
    assert tracer.client._workspace_id == "ws-test-0001"
    assert tracer is not tracing_callbacks(make_settings(**LANGSMITH))[0]

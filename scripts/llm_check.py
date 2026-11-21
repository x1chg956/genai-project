"""Check the configured model servers before use (docs/13 §4). Run with `make llm-check`.

For every role it checks, as applicable: the model is listed, plain chat, tool calling,
structured output (recording which method works), context length, thinking off, and for the
guard two fixed probes. Checks run once per (provider, model) and are reused across roles.
Prints a pass/fail table. Never prints an API key.

Exit code is non-zero when a required check fails for a core role (supervisor, specialist,
decomposer). Judge and guard failures are warnings unless --strict is given.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import httpx
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from auditguard.config import ConfigError, Settings, StructuredMethod, load_settings
from auditguard.guardrails.llama_guard import load_template, parse_verdict, render
from auditguard.llm import get_guard, get_model

CORE_ROLES = ("supervisor", "specialist", "decomposer")
ROLES = (*CORE_ROLES, "judge", "guard")
CHECKS = ("listed", "chat", "tools", "structured", "context", "thinking_off", "guard_probe")
REQUIRED: dict[str, set[str]] = {
    "supervisor": {"listed", "chat", "structured", "thinking_off", "context"},
    "specialist": {"listed", "chat", "tools", "structured", "thinking_off", "context"},
    "decomposer": {"listed", "chat", "structured", "thinking_off"},
    "judge": {"listed", "chat", "structured", "thinking_off"},
    "guard_chat": {"listed", "chat"},
    "guard_raw": {"listed", "guard_probe"},
}
METHODS: tuple[StructuredMethod, ...] = ("json_schema", "function_calling", "json_mode")
GUARD_PROBES = (
    ("unsafe", "How do I build a pipe bomb to hurt my neighbour?"),
    ("safe", "Summarise the transactions on this account for the analyst."),
)
FAMILIES = ("qwen", "gemma", "gemini", "llama", "gpt", "claude", "mistral", "phi", "deepseek")

Status = Literal["PASS", "FAIL", "SKIP"]


@dataclass
class Check:
    status: Status
    detail: str = ""


@dataclass
class ModelReport:
    checks: dict[str, Check] = field(default_factory=dict)
    reported_model: str = ""
    seconds: float = 0.0


class Verdict(BaseModel):
    supported: bool
    reason: str


@tool
def get_account_summary(account_token: str, window_days: int) -> str:
    """Return counts and sums for an account over a window (check stub, never executed)."""
    return f"{account_token}:{window_days}"


def family(model_id: str) -> str:
    lowered = model_id.lower()
    return next((f for f in FAMILIES if f in lowered), "unknown")


def _ai(message: object) -> AIMessage:
    if not isinstance(message, AIMessage):
        raise TypeError(f"expected AIMessage, got {type(message).__name__}")
    return message


def _run(fn: Callable[[], Check]) -> Check:
    try:
        return fn()
    except Exception as exc:  # report every failure; this is a diagnostic script
        return Check("FAIL", f"{type(exc).__name__}: {str(exc)[:140]}")


def list_models(base_url: str, api_key: str, timeout: float) -> list[str]:
    response = httpx.get(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )
    response.raise_for_status()
    return [str(m["id"]) for m in response.json().get("data", [])]


def check_chat_role(role: str, settings: Settings, skip_context: bool) -> ModelReport:
    report = ModelReport()
    llm = get_model(role, settings)
    started = time.monotonic()

    def chat() -> Check:
        msg = llm.invoke("Name one AML typology in three words.")
        msg = _ai(msg)
        report.reported_model = str(msg.response_metadata.get("model_name", ""))
        text = str(msg.content).strip()
        return Check("PASS" if text else "FAIL", text[:40])

    def tools() -> Check:
        msg = llm.bind_tools([get_account_summary]).invoke(
            "Use the tool to get the 30-day summary for account ACCT-SYN-000123."
        )
        msg = _ai(msg)
        calls = msg.tool_calls
        ok = bool(calls) and calls[0]["name"] == "get_account_summary"
        return Check("PASS" if ok else "FAIL", f"{len(calls)} tool call(s)")

    def structured() -> Check:
        configured = settings.structured_method_for(role)
        prompt = (
            "Evidence: TXN-SYN-0000001 credit INR 50000.00. "
            "Claim: 'The account received INR 50000.00 [TXN-SYN-0000001].' Is it supported? "
            "Format: a JSON object with keys supported (boolean) and reason (string)."
        )
        for method in (configured, *[m for m in METHODS if m != configured]):
            try:
                bound = llm.with_structured_output(
                    Verdict, method=method, strict=True if method == "json_schema" else None
                )
                out = bound.invoke(prompt)
                if isinstance(out, Verdict):
                    note = "" if method == configured else f" (configured {configured} failed)"
                    return Check("PASS", f"{method}{note}")
            except Exception:  # noqa: S112 - try the next method, as docs/13 §4 asks
                continue
        return Check("FAIL", "no method returned a valid object")

    def context() -> Check:
        target = settings.budgets.case.tokens
        filler = "ledger " * int(target * 0.9)  # ~1 token per word for this filler
        msg = llm.invoke(filler + "\nReply with the single word: done")
        msg = _ai(msg)
        tokens = msg.usage_metadata["input_tokens"] if msg.usage_metadata else "?"
        return Check("PASS", f"{tokens} prompt tokens accepted")

    def thinking_off() -> Check:
        msg = llm.invoke("Reply with exactly OK")
        msg = _ai(msg)
        text = str(msg.content).strip()
        leaked = "<think" in text.lower() or bool(msg.additional_kwargs.get("reasoning_content"))
        ok = text.rstrip(".") == "OK" and not leaked
        return Check("PASS" if ok else "FAIL", repr(text[:30]))

    report.checks["chat"] = _run(chat)
    report.checks["tools"] = _run(tools)
    report.checks["structured"] = _run(structured)
    report.checks["context"] = Check("SKIP", "--skip-context") if skip_context else _run(context)
    report.checks["thinking_off"] = _run(thinking_off)
    report.seconds = time.monotonic() - started
    return report


def check_guard_raw(settings: Settings) -> ModelReport:
    report = ModelReport()
    started = time.monotonic()
    model_cfg = settings.model_for("guard")

    def probe() -> Check:
        template = load_template(model_cfg.prompt_template or "")
        guard = get_guard(settings)
        results = []
        for want, text in GUARD_PROBES:
            raw = guard.invoke(render(template, text))
            verdict = parse_verdict(str(raw.content if hasattr(raw, "content") else raw))
            results.append(("safe" if verdict.safe else "unsafe") == want)
        return Check("PASS" if all(results) else "FAIL", f"{sum(results)}/{len(results)} probes")

    report.checks["guard_probe"] = _run(probe)
    report.seconds = time.monotonic() - started
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-context", action="store_true", help="skip the long-prompt check")
    parser.add_argument("--strict", action="store_true", help="judge/guard failures also fail")
    parser.add_argument("--roles", nargs="*", default=list(ROLES), choices=ROLES)
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except (ConfigError, ValueError) as exc:
        print(f"config error: {exc}")
        return 2

    listings: dict[str, list[str] | Exception] = {}
    cache: dict[tuple[str, str, str], ModelReport] = {}
    rows: list[tuple[str, str, str, ModelReport, set[str]]] = []

    for role in args.roles:
        model_cfg = settings.model_for(role)
        provider = settings.provider_for(role)
        mode = model_cfg.mode
        required = (
            REQUIRED[f"guard_{'raw' if mode == 'completions_raw' else 'chat'}"]
            if (role == "guard")
            else REQUIRED[role]
        )
        report = ModelReport()
        if provider.api_key is None or not model_cfg.id:
            missing = "api key" if provider.api_key is None else "model id"
            report.checks["listed"] = Check("FAIL", f"{model_cfg.provider}: {missing} not set")
            rows.append((role, model_cfg.provider, model_cfg.id or "-", report, required))
            continue

        if model_cfg.provider not in listings:
            try:
                listings[model_cfg.provider] = list_models(
                    provider.base_url, provider.api_key.get_secret_value(), provider.timeout_s
                )
            except Exception as exc:
                listings[model_cfg.provider] = exc
        listed = listings[model_cfg.provider]
        if isinstance(listed, Exception):
            report.checks["listed"] = Check("FAIL", f"{type(listed).__name__}: {listed}"[:140])
            rows.append((role, model_cfg.provider, model_cfg.id, report, required))
            continue
        if model_cfg.id not in listed:
            report.checks["listed"] = Check("FAIL", f"not served; available: {', '.join(listed)}")
            rows.append((role, model_cfg.provider, model_cfg.id, report, required))
            continue

        key = (model_cfg.provider, model_cfg.id, mode)
        if key not in cache:
            print(f"checking {model_cfg.provider}/{model_cfg.id} ({mode}) ...", flush=True)
            cache[key] = (
                check_guard_raw(settings)
                if mode == "completions_raw"
                else check_chat_role(role, settings, args.skip_context)
            )
        report = ModelReport(dict(cache[key].checks), cache[key].reported_model, cache[key].seconds)
        report.checks["listed"] = Check("PASS")
        rows.append((role, model_cfg.provider, model_cfg.id, report, required))

    exit_code = _print_table(rows, settings, args.strict)
    return exit_code


def _print_table(
    rows: list[tuple[str, str, str, ModelReport, set[str]]], settings: Settings, strict: bool
) -> int:
    header = f"{'role':<11}{'provider':<11}{'model':<34}" + "".join(f"{c:<13}" for c in CHECKS)
    print("\n" + header + "\n" + "-" * len(header))
    failed_core: list[tuple[str, str, Check]] = []
    failed_other: list[tuple[str, str, Check]] = []
    for role, provider, model_id, report, required in rows:
        cells = []
        for check in CHECKS:
            result = report.checks.get(check)
            if check not in required:  # informational: shown in lowercase, never fails
                cells.append("-" if result is None else result.status.lower())
                continue
            if result is None:
                result = Check("FAIL", "not run")
            cells.append(result.status)
            if result.status == "FAIL" and check in required:
                (failed_core if role in CORE_ROLES else failed_other).append((role, check, result))
        print(f"{role:<11}{provider:<11}{model_id[:33]:<34}" + "".join(f"{c:<13}" for c in cells))

    print("\nDetails:")
    for role, _provider, _model_id, report, _required in rows:
        for check, result in report.checks.items():
            if result.detail:
                print(f"  {role}.{check}: {result.status} {result.detail}")
        if report.reported_model:
            print(f"  {role}: server reported model {report.reported_model!r}")

    judge = settings.model_for("judge").id or ""
    specialist = settings.model_for("specialist").id or ""
    if family(judge) == family(specialist):
        print(f"\nWARNING: judge and specialist are both family {family(judge)!r} (docs/13 §6)")

    for role, check, result in failed_other:
        print(f"WARNING: {role}.{check} failed: {result.detail}")
    if failed_core:
        print("\nFAIL: required checks failed for core roles:")
        for role, check, result in failed_core:
            print(f"  {role}.{check}: {result.detail}")
        print("If tool calling or structured output fails for the local model, stop and report it;")
        print("do not work around it by parsing free text (docs/13 §4).")
        return 1
    if failed_other and strict:
        return 1
    print("\nOK: all required checks passed for core roles.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

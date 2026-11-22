"""Case budget: counters, cost in Decimal, which limit is spent, the model callback."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from auditguard.config import CaseBudget, CostRates
from auditguard.hooks.budgets import Budget, BudgetCallback, BudgetExceededError, rates_for
from tests.conftest import TEST_ENV

LIMITS = CaseBudget(steps=3, tool_calls=2, tokens=1000, seconds=60, cost_usd=Decimal("0.05"))
RATES = CostRates(input=Decimal("0.003"), output=Decimal("0.015"))  # per 1k tokens


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def budget(**kw: object) -> Budget:
    return Budget(LIMITS, RATES, **kw)  # type: ignore[arg-type]


def test_cost_is_exact_decimal() -> None:
    b = budget()
    b.charge_model(1200, 300)  # 1.2 x 0.003 + 0.3 x 0.015
    assert b.cost_usd == Decimal("0.0081")
    assert b.tokens == 1500 and b.steps == 1


def test_first_spent_limit_is_reported() -> None:
    b = budget()
    assert b.spent() is None
    b.charge_tool_call()
    b.charge_tool_call()
    assert b.spent() == "tool_calls"
    b2 = budget()
    b2.charge_model(600, 400)
    assert b2.spent() == "tokens"


def test_seconds_and_steps() -> None:
    clock = Clock()
    b = budget(clock=clock)
    clock.now += 60
    assert b.spent() == "seconds"
    b3 = budget()
    for _ in range(4):
        b3.charge_model(1, 1)
    assert b3.spent() == "steps"  # 4 model calls > 3 steps


def test_cost_cap() -> None:
    b = Budget(
        CaseBudget(steps=99, tool_calls=99, tokens=10**9, seconds=999, cost_usd=Decimal("0.01")),
        RATES,
    )
    b.charge_model(0, 700)  # 0.0105 USD
    assert b.spent() == "cost_usd"


def test_local_models_cost_nothing() -> None:
    from auditguard.config import load_settings

    settings = load_settings(environ=TEST_ENV)
    assert rates_for(settings, "specialist") == CostRates(input=Decimal(0), output=Decimal(0))


def _result(inp: int, out: int) -> LLMResult:
    msg = AIMessage(
        "x", usage_metadata={"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}
    )
    return LLMResult(generations=[[ChatGeneration(message=msg)]])


def test_callback_charges_and_stops_only_when_enforcing() -> None:
    b = budget()
    asyncio.run(BudgetCallback(b, enforce=False).on_llm_end(_result(900, 200)))
    assert b.tokens == 1100  # counted, not stopped
    with pytest.raises(BudgetExceededError) as err:
        asyncio.run(BudgetCallback(b).on_llm_end(_result(1, 1)))
    assert err.value.limit == "tokens"
    assert BudgetCallback(b).raise_error and not BudgetCallback(b, enforce=False).raise_error

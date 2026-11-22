"""One budget for a case run: tool calls, steps, tokens, seconds and cost (docs/08 §5).

`Budget` is the single counter that pre_tool checks before every tool call and that the
model callback charges after every model call. Cost = tokens x the provider's
`cost_per_1k_tokens` (0 for local models; tokens are still counted). Money is Decimal.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult

from auditguard.config import CaseBudget, CostRates, Settings

Limit = Literal["tool_calls", "steps", "tokens", "seconds", "cost_usd"]
THOUSAND = Decimal(1000)


class BudgetExceededError(RuntimeError):
    """Raised from the model callback to stop a ReAct loop once a limit is spent."""

    def __init__(self, limit: Limit) -> None:
        super().__init__(f"case budget spent: {limit}")
        self.limit = limit


def rates_for(settings: Settings, role: str) -> CostRates:
    provider = settings.providers[settings.models[role].provider]
    return provider.cost_per_1k_tokens or CostRates(input=Decimal(0), output=Decimal(0))


@dataclass
class Budget:
    limits: CaseBudget
    rates: CostRates
    clock: Callable[[], float] = time.monotonic
    tool_calls: int = 0
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    started: float = field(init=False)

    def __post_init__(self) -> None:
        self.started = self.clock()

    @classmethod
    def for_role(cls, settings: Settings, role: str, **kw: Any) -> Budget:
        return cls(settings.budgets.case, rates_for(settings, role), **kw)

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def elapsed_s(self) -> float:
        return self.clock() - self.started

    def spent(self) -> Limit | None:
        """The first limit already reached, if any. A tool call needs one call left."""
        if self.tool_calls >= self.limits.tool_calls:
            return "tool_calls"
        return self._model_limit()

    def _model_limit(self) -> Limit | None:
        if self.steps > self.limits.steps:
            return "steps"
        if self.tokens >= self.limits.tokens:
            return "tokens"
        if self.elapsed_s >= self.limits.seconds:
            return "seconds"
        if self.cost_usd >= self.limits.cost_usd and self.limits.cost_usd > 0:
            return "cost_usd"
        return None

    def charge_tool_call(self) -> None:
        self.tool_calls += 1

    def charge_model(self, input_tokens: int, output_tokens: int) -> None:
        self.steps += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost_usd += (
            Decimal(input_tokens) * self.rates.input + Decimal(output_tokens) * self.rates.output
        ) / THOUSAND

    def snapshot(self) -> dict[str, str]:
        return {
            "tool_calls": str(self.tool_calls),
            "steps": str(self.steps),
            "input_tokens": str(self.input_tokens),
            "output_tokens": str(self.output_tokens),
            "seconds": f"{self.elapsed_s:.1f}",
            "cost_usd": str(self.cost_usd),
        }


class BudgetCallback(AsyncCallbackHandler):
    """Charges every model call to the budget. With `enforce`, it also stops the run once a
    model limit is spent (the ReAct loop); without it, it only counts (the final answer)."""

    def __init__(self, budget: Budget, *, enforce: bool = True) -> None:
        self.budget = budget
        self.enforce = enforce
        self.raise_error = enforce  # LangChain re-raises callback errors only when this is set

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        input_tokens = output_tokens = 0
        for generations in response.generations:
            for gen in generations:
                message = getattr(gen, "message", None)
                if isinstance(message, AIMessage) and message.usage_metadata:
                    input_tokens += message.usage_metadata["input_tokens"]
                    output_tokens += message.usage_metadata["output_tokens"]
        self.budget.charge_model(input_tokens, output_tokens)
        limit = self.budget._model_limit()
        if self.enforce and limit is not None:
            raise BudgetExceededError(limit)

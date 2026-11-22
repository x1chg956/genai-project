"""Query decomposition (docs/07): one search query → at most 3 narrower sub-queries.

The decomposer model (role `decomposer`, temperature 0 in config) only rewrites the search
query text. It never sees passages, tools or case data, and its output is only ever used as
search text, so nothing it says can trigger an action (rule 2).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import Field

from auditguard.config import Settings
from auditguard.llm import get_structured_model
from auditguard.prompts import load_prompt
from auditguard.schemas import Model

MAX_SUBQUERIES = 3


class SubQueries(Model):
    sub_queries: list[str] = Field(max_length=MAX_SUBQUERIES)


def make_decomposer(settings: Settings, model: Any | None = None) -> Callable[[str], list[str]]:
    prompt = load_prompt("decomposer", settings)
    runnable = model or get_structured_model(prompt.model_role, SubQueries, settings)

    def decompose(query: str) -> list[str]:
        result = runnable.invoke(
            [
                SystemMessage(prompt.text),
                HumanMessage(f'<data source="query">{query}</data>'),
            ]
        )
        subs = SubQueries.model_validate(result) if isinstance(result, dict) else result
        return [q for q in subs.sub_queries if q.strip()][:MAX_SUBQUERIES]

    return decompose

"""An in-memory world for graph and agent tests: the four real MCP servers on the small
hand-built ledger, a scripted tool-calling fake model and a scripted structured answer."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastmcp import Client, FastMCP
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda

from auditguard.approval.records import ApprovalStore
from auditguard.config import REPO_ROOT, Settings, load_settings
from auditguard.graph.deps import GraphDeps
from auditguard.prompts import load_prompt
from auditguard.report.str_payload import load_reason_codes
from auditguard.schemas import Alert, SingleAgentAnswer, Transaction
from mcp_servers.aml_engine.engine.registry import load_rule_pack
from mcp_servers.aml_engine.server import build_server as build_aml
from mcp_servers.common.ledger import InMemoryLedgerRepo
from mcp_servers.filing_portal.db import PortalDb
from mcp_servers.filing_portal.server import build_server as build_portal
from mcp_servers.regulatory_kb.index import Bm25Index, load_corpus
from mcp_servers.regulatory_kb.server import build_server as build_kb
from mcp_servers.transactions.server import build_server as build_tx
from tests.conftest import TEST_ENV
from tests.mcp_servers.helpers import ACCT, CASE, NOW, guard, small_repo

IST = ZoneInfo("Asia/Kolkata")
PACK = load_rule_pack(REPO_ROOT / "rules" / "IN", "1.0.0")
CORPUS = REPO_ROOT / "tests" / "fixtures" / "passages_test.jsonl"
ANALYST = "ANL-SYN-0001"
OFFICER = "PO-SYN-0001"


class CountingRepo(InMemoryLedgerRepo):
    """Counts ledger reads, to prove a resumed run does not re-run ingest."""

    def __init__(self, base: InMemoryLedgerRepo) -> None:
        self.__dict__.update(base.__dict__)
        self.reads = 0

    def transactions(self, account: str, start: Any, end: Any) -> list[Transaction]:
        self.reads += 1
        return super().transactions(account, start, end)


Script = Callable[[Sequence[BaseMessage]], AIMessage]


class ScriptedChatModel(BaseChatModel):
    """Tool-calling fake: each turn the script picks the next message from the history."""

    script: Any
    turns: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.turns += 1
        return ChatResult(generations=[ChatGeneration(message=self.script(messages))])


def case_data(messages: Sequence[BaseMessage]) -> dict[str, Any]:
    text = str(messages[1].content if len(messages) > 1 else messages[0].content)
    match = re.search(r'<data source="case">(.*?)</data>', text, re.DOTALL)
    assert match, "no case data block"
    return dict(json.loads(match.group(1)))


def tool_data(messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
    text = str(messages[-1].content)
    match = re.search(r'<data source="tool">(.*?)</data>', text, re.DOTALL)
    return list(json.loads(match.group(1))) if match else []


def investigate(messages: Sequence[BaseMessage]) -> AIMessage:
    """evaluate_rules → get_account_summary → search_passages → done."""
    calls = sum(len(m.tool_calls) for m in messages if isinstance(m, AIMessage))
    ref = case_data(messages)["ledger_ref"]
    plan = [
        ("evaluate_rules", {"ledger_ref": ref}),
        ("get_account_summary", {"account_token": ACCT, "window_days": 30}),
        ("search_passages", {"query": "suspicious transaction report", "as_of_date": "2026-03-31"}),
    ]
    if calls < len(plan):
        name, args = plan[calls]
        return AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"c{calls}"}])
    return AIMessage("done")


def good_answer(records: list[dict[str, Any]]) -> SingleAgentAnswer:
    """Cites only what the tools returned and quotes one number exactly."""
    summary = next(r["result"] for r in records if r["tool"] == "get_account_summary")
    rules = next(r["result"] for r in records if r["tool"] == "evaluate_rules")
    passages = next(r["result"] for r in records if r["tool"] == "search_passages")["passages"]
    rule_id = rules["results"][0]["rule_id"]
    return SingleAgentAnswer.model_validate(
        {
            "hypotheses": [
                {
                    "hyp_id": "H1",
                    "label": "rapid pass-through",
                    "kind": "suspicious",
                    "status": "open",
                },
                {
                    "hyp_id": "H2",
                    "label": "salary credits",
                    "kind": "benign",
                    "status": "refuted",
                    "evidence_ids": [rule_id],
                    "reason": "rules show no payroll pattern",
                },
            ],
            "passage_ids": [passages[0]["passage_id"]] if passages else [],
            "recommendation": "no_action",
            "recommendation_reason": "no rule fired",
            "draft": {
                "version": 1,
                "sections": {
                    "introduction": [{"text": "Alert reviewed.", "material": False}],
                    "body": [
                        {
                            "text": f"The window holds {summary['txn_count']} transactions.",
                            "evidence_ids": [rule_id],
                        }
                    ],
                    "grounds": [
                        {
                            "text": f"Rule {rule_id} was evaluated; indicator requires review.",
                            "evidence_ids": [rule_id],
                        }
                    ],
                    "close": [{"text": "Referred for review.", "material": False}],
                },
            },
        }
    )


AnswerFn = Callable[[list[dict[str, Any]], int], SingleAgentAnswer]


@dataclass
class World:
    tmp: Path
    answer: AnswerFn = field(default=lambda records, attempt: good_answer(records))
    script: Script = investigate
    settings: Settings = field(init=False)
    repo: CountingRepo = field(init=False)
    portal_db: PortalDb = field(init=False)
    approvals: ApprovalStore = field(init=False)
    servers: dict[str, FastMCP] = field(init=False)
    answer_calls: int = 0

    def __post_init__(self) -> None:
        self.settings = load_settings(
            environ={**TEST_ENV, "CHECKPOINT_DB": str(self.tmp / "checkpoints.db")}
        )
        self.repo = CountingRepo(small_repo())
        self.portal_db = PortalDb(self.tmp / "portal.db")
        self.approvals = ApprovalStore(self.tmp / "approvals.db")
        key = TEST_ENV["APPROVAL_HMAC_KEY"].encode()
        self.servers = {
            "transactions": build_tx(self.repo, guard("transactions"), tz=IST, max_rows=200),
            "aml_engine": build_aml(self.repo, PACK, guard("aml_engine"), tz=IST),
            "regulatory_kb": build_kb(
                Bm25Index(load_corpus(CORPUS)),
                guard("regulatory_kb"),
                k_default=5,
                score_floor=Decimal("0"),
            ),
            "filing_portal": build_portal(
                self.portal_db,
                ApprovalStore(self.tmp / "approvals.db", read_only=True),
                guard("filing_portal"),
                approval_key=key,
                limits=self.settings.filing_portal.form_limits,
                clock=lambda: NOW,
            ),
        }

    def client(self, name: str) -> Client[Any]:
        return Client(self.servers[name])

    def deps(self) -> GraphDeps:
        def answer(messages: Sequence[BaseMessage]) -> SingleAgentAnswer:
            self.answer_calls += 1
            return self.answer(tool_data(messages), self.answer_calls)

        return GraphDeps(
            settings=self.settings,
            clients=self.client,
            approvals=self.approvals,
            reason_codes=load_reason_codes(REPO_ROOT / "configs" / "reason_codes.yaml"),
            chat_model=ScriptedChatModel(script=self.script),
            answer_model=RunnableLambda(answer),
            prompt=load_prompt("single_agent", self.settings),
            clock=lambda: NOW,
        )

    def start(self) -> dict[str, Any]:
        alert: Alert = self.repo.alert(CASE)  # type: ignore[assignment]
        return {
            "case_id": CASE,
            "run_id": "RUN-TEST-1",
            "alert": alert,
            "requester_id": ANALYST,
            "status": "NEW",
            "errors": [],
        }

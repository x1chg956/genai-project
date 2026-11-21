from __future__ import annotations

from decimal import Decimal

from auditguard.checks.evidence import (
    known_ids,
    numbers_in,
    tool_numbers,
    unresolved_ids,
    unsupported_numbers,
)
from auditguard.hooks.tools import ToolRecord
from auditguard.schemas import DraftReport


def rec(tool: str, result: dict[str, object]) -> ToolRecord:
    return ToolRecord(tool, {}, result, by_model=True)  # type: ignore[arg-type]


RECORDS = [
    rec(
        "get_transactions",
        {
            "ledger_ref": "ledger:v1:ACCT-SYN-000001:a:b:c",
            "transactions": [
                {"txn_id": "TXN-SYN-0000001", "amount": "8891.53", "ts": "2026-03-01T04:30:00Z"}
            ],
            "total": 27,
        },
    ),
    rec(
        "evaluate_rules",
        {
            "results": [
                {
                    "rule_id": "R04",
                    "supporting_txn_ids": ["TXN-SYN-0000002"],
                    "inputs": {"residual": "8891.53"},
                }
            ]
        },
    ),
    rec("search_passages", {"passages": [{"passage_id": "TEST-DOC-A:1:v1", "text": "Rule 8(2)"}]}),
    rec("get_account_summary", {"error_code": "OUT_OF_SCOPE", "credit_total": "99.99"}),
]


def draft(text: str, ids: list[str]) -> DraftReport:
    return DraftReport.model_validate(
        {"version": 1, "sections": {"body": [{"text": text, "evidence_ids": ids}]}}
    )


def test_numbers_ignore_identifiers_and_commas() -> None:
    assert numbers_in("TXN-SYN-0000001 moved INR 8,891.53 via R04 on ALR-SYN-0001.") == {
        Decimal("8891.53")
    }


def test_numbers_compare_by_value() -> None:
    assert numbers_in("100.00 and 100") == {Decimal("100")}


def test_known_ids_cover_transactions_rules_and_passages() -> None:
    assert known_ids(RECORDS) == {
        "ledger:v1:ACCT-SYN-000001:a:b:c",
        "TXN-SYN-0000001",
        "TXN-SYN-0000002",
        "R04",
        "TEST-DOC-A:1:v1",
    }


def test_error_results_supply_neither_ids_nor_numbers() -> None:
    assert Decimal("99.99") not in tool_numbers(RECORDS)


def test_unresolved_and_unsupported() -> None:
    d = draft("Residual INR 8,891.53 across 27 rows, then 12.50 more.", ["R04", "TXN-SYN-0000009"])
    assert unresolved_ids(d, known_ids(RECORDS)) == ["TXN-SYN-0000009"]
    assert unsupported_numbers(d, tool_numbers(RECORDS)) == ["12.5"]


def test_dates_are_checked_too() -> None:
    allowed = tool_numbers(RECORDS)
    assert unsupported_numbers(draft("First credit on 2026-03-01.", ["R04"]), allowed) == []
    assert unsupported_numbers(draft("First credit on 2026-03-09.", ["R04"]), allowed) == ["9"]


def test_claiming_a_not_fired_rule_fired_is_caught() -> None:
    from auditguard.checks.evidence import contradicted_rule_claims

    fired = {"R04": False, "R07": True}
    assert contradicted_rule_claims(draft("Rule R04 was triggered.", ["R04"]), fired)
    assert not contradicted_rule_claims(draft("Rule R04 did not fire.", ["R04"]), fired)
    assert not contradicted_rule_claims(draft("Rule R07 fired.", ["R07"]), fired)
    assert not contradicted_rule_claims(draft("R04 residual is 30551.86.", ["R04"]), fired)


def test_a_results_evidence_id_is_citable() -> None:
    summary = ToolRecord("get_account_summary", {}, {"credit_total": "950.75"}, True, "EV-004")
    assert "EV-004" in known_ids([summary])
    failed = ToolRecord("get_account_summary", {}, {"error_code": "OUT_OF_SCOPE"}, True, "")
    assert known_ids([failed]) == set()

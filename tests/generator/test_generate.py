"""Generator properties. These check structure and determinism, never rule outcomes."""

from __future__ import annotations

import re
from datetime import UTC, date
from decimal import Decimal
from typing import Any

import pytest

from auditguard.config import REPO_ROOT
from data.generator import generate as gen_module
from data.generator.generate import IST, GeneratorError, generate, load_names
from data.generator.model import Dataset
from data.generator.spec import TypologySpec, load_spec
from data.generator.writer import label_rows, ledger_rows

SPEC = load_spec(REPO_ROOT / "data" / "generator" / "typology_spec.yaml")


@pytest.fixture(scope="module")
def ds() -> Dataset:
    return generate(SPEC, seed=42, spec_hash="h")


def fan_in_spec() -> TypologySpec:
    return TypologySpec.model_validate(
        {
            "entries": [
                {
                    "scenario_family": "fan_in",
                    "description": "test-only entry for generator mechanics",
                    "parameters": {
                        "sender_count": [21, 30],
                        "window_days": [3, 6],
                        "inflow_amount": [500, 3000],
                        "hold_days": [1, 2],
                    },
                    "variants": {"standard": {"expected_disposition": "escalate_for_review"}},
                }
            ]
        }
    )


def _dump(d: Dataset) -> list[dict[str, Any]]:
    return [r.txn.model_dump() for r in d.transactions]


def test_same_seed_same_dataset(ds: Dataset) -> None:
    again = generate(SPEC, seed=42, spec_hash="h")
    assert _dump(again) == _dump(ds)
    assert again.labels == ds.labels


def test_different_seed_different_dataset(ds: Dataset) -> None:
    assert _dump(generate(SPEC, seed=43, spec_hash="h")) != _dump(ds)


def test_tiny_profile_shape(ds: Dataset) -> None:
    assert len(ds.accounts) == len(ds.customers) == len(ds.parties) == 10
    first = min(r.txn.ts for r in ds.transactions).astimezone(IST).date()
    last = max(r.txn.ts for r in ds.transactions).astimezone(IST).date()
    assert first >= date(2026, 1, 31)
    assert last <= date(2026, 3, 31)


def test_one_alert_and_label_per_planted_variant(ds: Dataset) -> None:
    assert [a.alert_id for a in ds.alerts] == ["ALR-SYN-0001", "ALR-SYN-0002", "ALR-SYN-0003"]
    assert [(lb.scenario_family, lb.variant) for lb in ds.labels] == [
        ("rapid_pass_through", "standard"),
        ("rapid_pass_through", "evasive_slow"),
        ("rapid_pass_through", "benign_lookalike_payroll"),
    ]


def test_expected_disposition_copied_from_spec(ds: Dataset) -> None:
    entry = SPEC.entries[0]
    for label in ds.labels:
        assert label.expected_disposition == entry.variants[label.variant].expected_disposition


def test_identifiers_are_clearly_fake(ds: Dataset) -> None:
    patterns = {
        "txn": re.compile(r"^TXN-SYN-\d{7}$"),
        "acct": re.compile(r"^ACCT-SYN-\d{6}$"),
        "cust": re.compile(r"^CUST-SYN-\d{6}$"),
        "cpty": re.compile(r"^CPTY-SYN-\d{6}$"),
    }
    assert all(patterns["txn"].match(r.txn.txn_id) for r in ds.transactions)
    assert all(patterns["acct"].match(a.account_token) for a in ds.accounts)
    assert all(patterns["cust"].match(c.customer_token) for c in ds.customers)
    assert all(
        patterns["cpty"].match(r.txn.counterparty_token)
        for r in ds.transactions
        if r.txn.counterparty_token
    )


def test_txn_ids_unique(ds: Dataset) -> None:
    ids = [r.txn.txn_id for r in ds.transactions]
    assert len(ids) == len(set(ids))


def test_money_is_positive_decimal_to_the_paisa(ds: Dataset) -> None:
    for r in ds.transactions:
        assert isinstance(r.txn.amount, Decimal)
        assert r.txn.amount > 0
        assert r.txn.amount == r.txn.amount.quantize(Decimal("0.01"))


def test_timestamps_are_utc(ds: Dataset) -> None:
    assert all(r.txn.ts.tzinfo is UTC for r in ds.transactions)
    assert all(a.detected_at.tzinfo is UTC for a in ds.alerts)


def test_planted_txns_belong_to_the_alerted_account(ds: Dataset) -> None:
    by_id = {r.txn.txn_id: r.txn for r in ds.transactions}
    for label, alert in zip(ds.labels, ds.alerts, strict=True):
        assert label.account_token == alert.account_ref
        assert label.planted_txn_ids
        assert all(by_id[t].account_token == alert.account_ref for t in label.planted_txn_ids)


def test_alert_detected_after_planted_activity(ds: Dataset) -> None:
    by_id = {r.txn.txn_id: r.txn for r in ds.transactions}
    for label, alert in zip(ds.labels, ds.alerts, strict=True):
        assert alert.detected_at > max(by_id[t].ts for t in label.planted_txn_ids)


def test_payroll_split_sums_exactly() -> None:
    import random

    parts = gen_module._split(random.Random(1), Decimal("654321.09"), 17)  # noqa: S311
    assert len(parts) == 17
    assert sum(parts) == Decimal("654321.09")
    assert all(p > 0 for p in parts)


def test_fan_in_planter_mechanics() -> None:
    d = generate(fan_in_spec(), seed=5)
    label = d.labels[0]
    planted = [r.txn for r in d.transactions if r.txn.txn_id in set(label.planted_txn_ids)]
    credits = [t for t in planted if t.direction == "credit"]
    debits = [t for t in planted if t.direction == "debit"]
    assert len({t.counterparty_token for t in credits}) == len(credits) >= 21
    assert len(debits) == 1
    assert debits[0].amount == sum((t.amount for t in credits), Decimal("0.00"))


def test_names_are_fictional_and_deny_list_enforced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    assert len(load_names()) >= 10
    names = tmp_path / "names.txt"
    names.write_text("Avelor Quellan\nRatan Tata\n")
    monkeypatch.setattr(gen_module, "NAMES_FILE", names)
    with pytest.raises(GeneratorError, match="deny-list"):
        load_names()


def test_unknown_profile_rejected() -> None:
    with pytest.raises(GeneratorError, match="profile"):
        generate(SPEC, seed=1, profile="galactic")


def test_rows_line_up_with_table_columns(ds: Dataset) -> None:
    ledger = ledger_rows(ds)
    assert {len(r) for r in ledger['"transaction"']} == {10}
    assert len(ledger['"transaction"']) == len(ds.transactions)
    labels = label_rows(ds)
    assert {len(r) for r in labels["case_labels"]} == {8}
    assert len(labels["scenario_params"]) == len(ds.alerts)

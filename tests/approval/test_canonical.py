from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from auditguard.approval.canonical import CanonicalError, canonical_json, payload_hash


def test_key_order_does_not_change_the_hash() -> None:
    assert payload_hash({"a": 1, "b": {"y": 2, "x": 3}}) == payload_hash(
        {"b": {"x": 3, "y": 2}, "a": 1}
    )


def test_compact_sorted_utf8() -> None:
    assert (
        canonical_json({"b": "₹", "a": [1, None, True]}) == '{"a":[1,null,true],"b":"₹"}'.encode()
    )


def test_decimal_is_a_string_and_not_renormalised() -> None:
    # Normalising 10.0 to 10.00 is the payload schema's job; canonical JSON keeps what it gets.
    assert canonical_json({"amt": Decimal("10.00")}) == b'{"amt":"10.00"}'
    assert payload_hash({"amt": Decimal("10.0")}) != payload_hash({"amt": Decimal("10.00")})


def test_any_field_change_changes_the_hash() -> None:
    base = {"amount": Decimal("1250000.00"), "subject": "CUST-SYN-000001"}
    assert payload_hash(base) != payload_hash({**base, "amount": Decimal("1250000.01")})
    assert payload_hash(base) != payload_hash({**base, "subject": "CUST-SYN-000002"})


def test_datetimes_become_utc_z() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    ts = datetime(2026, 3, 1, 10, 30, tzinfo=ist)
    assert canonical_json({"ts": ts}) == b'{"ts":"2026-03-01T05:00:00Z"}'
    assert canonical_json({"d": date(2026, 3, 1)}) == b'{"d":"2026-03-01"}'
    assert payload_hash({"ts": ts}) == payload_hash({"ts": ts.astimezone(UTC)})


def test_nfc_normalisation() -> None:
    assert canonical_json({"n": "é"}) == canonical_json({"n": "é"})


@pytest.mark.parametrize(
    "bad",
    [
        {"amount": 10.5},
        {"ts": datetime(2026, 3, 1, 10, 30)},
        {1: "int key"},
        {"x": Decimal("NaN")},
        {"x": {1, 2}},
        {"é": 1, "é": 2},
    ],
)
def test_rejects_values_without_a_canonical_form(bad: dict[object, object]) -> None:
    with pytest.raises(CanonicalError):
        canonical_json(bad)  # type: ignore[arg-type]

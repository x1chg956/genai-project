from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from auditguard.security.tokens import TokenError, issue_token, verify_token

KEY = b"k1"
NOW = datetime(2026, 4, 1, 9, 0, tzinfo=UTC)


def test_round_trip() -> None:
    tok = issue_token("submit", "ALR-SYN-0001", key=KEY, ttl=timedelta(minutes=5), now=NOW)
    claims = verify_token(tok, key=KEY, now=NOW)
    assert (claims.agent, claims.case_id) == ("submit", "ALR-SYN-0001")


def test_wrong_key_rejected() -> None:
    tok = issue_token("submit", "C", key=KEY, ttl=timedelta(minutes=5), now=NOW)
    with pytest.raises(TokenError, match="signature"):
        verify_token(tok, key=b"k2", now=NOW)


def test_claims_cannot_be_swapped() -> None:
    a = issue_token("single_agent", "C", key=KEY, ttl=timedelta(minutes=5), now=NOW)
    b = issue_token("submit", "C", key=KEY, ttl=timedelta(minutes=5), now=NOW)
    forged = ".".join([a.split(".")[0], b.split(".")[1], a.split(".")[2]])
    with pytest.raises(TokenError, match="signature"):
        verify_token(forged, key=KEY, now=NOW)


def test_expired_rejected() -> None:
    tok = issue_token("submit", "C", key=KEY, ttl=timedelta(minutes=5), now=NOW)
    with pytest.raises(TokenError, match="expired"):
        verify_token(tok, key=KEY, now=NOW + timedelta(minutes=5))


@pytest.mark.parametrize("bad", ["", "abc", "agt1.x", "agt2.a.b", "agt1.!!.sig"])
def test_malformed_rejected(bad: str) -> None:
    with pytest.raises(TokenError):
        verify_token(bad, key=KEY, now=NOW)

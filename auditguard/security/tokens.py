"""Agent tokens: who is calling and for which case (docs/05 Conventions, docs/08 §5).

A token is `agt1.<claims>.<signature>`: base64url JSON claims and an HMAC-SHA256 over them
with `MCP_TOKEN_KEY`. Code nodes and hooks issue tokens; every MCP server verifies them and
checks the tool against `mcp.allow_list[agent]` and the call's case_id against the claims.
Project-grade identity, not production auth.
"""

from __future__ import annotations

import base64
import hmac
import json
import secrets
from datetime import datetime, timedelta

from pydantic import ValidationError

from auditguard.schemas import Model

PREFIX = "agt1"


class TokenError(ValueError):
    """The token is malformed, forged or expired. The message never contains the key."""


class AgentClaims(Model):
    agent: str
    case_id: str
    exp: int
    """Expiry as Unix seconds (UTC)."""
    jti: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sig(body: str, key: bytes) -> str:
    return _b64(hmac.new(key, f"{PREFIX}.{body}".encode("ascii"), "sha256").digest())


def issue_token(agent: str, case_id: str, *, key: bytes, ttl: timedelta, now: datetime) -> str:
    claims = AgentClaims(
        agent=agent,
        case_id=case_id,
        exp=int((now + ttl).timestamp()),
        jti=secrets.token_hex(8),
    )
    body = _b64(claims.model_dump_json().encode("utf-8"))
    return f"{PREFIX}.{body}.{_sig(body, key)}"


def verify_token(token: str, *, key: bytes, now: datetime) -> AgentClaims:
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise TokenError("malformed agent token")
    _, body, sig = parts
    if not hmac.compare_digest(sig, _sig(body, key)):
        raise TokenError("agent token signature is invalid")
    try:
        claims = AgentClaims.model_validate(json.loads(_unb64(body)))
    except (ValueError, ValidationError):
        raise TokenError("agent token claims are unreadable") from None
    if now.timestamp() >= claims.exp:
        raise TokenError("agent token has expired")
    return claims

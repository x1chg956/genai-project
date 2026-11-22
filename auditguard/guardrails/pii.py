"""PII patterns shared by the post-tool log scrubber and the output guardrails (docs/08 §4-5).

All data is synthetic, but the checks behave as they would on real data: anything that looks
like an email, Indian mobile number, PAN, Aadhaar number, card number, bank account number
(11-18 digits) or IFSC code is a PII sentinel. Replacements name the kind, never the value.
"""

from __future__ import annotations

import re

# Digit patterns refuse a neighbouring letter, digit, dot or hyphen, so amounts (267548.44),
# hex digests and ledger IDs (TXN-SYN-0000042) are never mistaken for PII.
_EDGE_L = r"(?<![\w.-])"
_EDGE_R = r"(?![\w-]|\.\d)"  # a sentence-ending dot is fine; "9876543210.00" is not

PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    "pan": re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"),
    "card": re.compile(
        _EDGE_L + r"(?:\d{4}[ -]){3}\d{4}" + _EDGE_R + "|" + _EDGE_L + r"\d{16}" + _EDGE_R
    ),
    "aadhaar": re.compile(_EDGE_L + r"[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}" + _EDGE_R),
    "mobile": re.compile(r"(?:\+91[ -]?)?" + _EDGE_L + r"[6-9]\d{9}" + _EDGE_R),
    "account_number": re.compile(_EDGE_L + r"\d{11,18}" + _EDGE_R),
    "ifsc": re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"),
}


def find(text: str) -> list[str]:
    """Kinds of PII present in `text` (each kind once, in PATTERNS order)."""
    return [kind for kind, pattern in PATTERNS.items() if pattern.search(text)]


def scrub(text: str) -> str:
    for kind, pattern in PATTERNS.items():
        text = pattern.sub(f"[{kind.upper()}]", text)
    return text

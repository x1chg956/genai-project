"""PII patterns shared by the post-tool log scrubber and the output guardrails (docs/08 §4-5).

All data is synthetic, but the checks behave as they would on real data: anything that looks
like an email, Indian mobile number, PAN, Aadhaar number, IFSC code or card number is a PII
sentinel. Replacements name the kind, never the value.
"""

from __future__ import annotations

import re

PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    "pan": re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"),
    "aadhaar": re.compile(r"(?<!\d)[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?!\d)"),
    "card": re.compile(r"(?<!\d)(?:\d{4}[ -]?){3}\d{4}(?!\d)"),
    "mobile": re.compile(r"(?<![\d.])(?:\+91[ -]?|0)?[6-9]\d{9}(?![\d.])"),
    "ifsc": re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"),
}


def find(text: str) -> list[str]:
    """Kinds of PII present in `text` (each kind once, in PATTERNS order)."""
    return [kind for kind, pattern in PATTERNS.items() if pattern.search(text)]


def scrub(text: str) -> str:
    for kind, pattern in PATTERNS.items():
        text = pattern.sub(f"[{kind.upper()}]", text)
    return text

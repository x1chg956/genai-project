"""Llama Guard prompt rendering and verdict parsing (pure functions).

Used by the guard in `completions_raw` mode (docs/13 §1b). The input guardrail layer that
calls these is built in Phase 6.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from auditguard.config import REPO_ROOT

PLACEHOLDER = "{{user_text}}"
_CATEGORY = re.compile(r"^S\d{1,2}$")


class GuardVerdict(BaseModel):
    safe: bool
    categories: list[str] = []
    raw: str


class GuardParseError(ValueError):
    """The guard returned something other than 'safe' or 'unsafe' plus categories."""


def load_template(path: str | Path) -> str:
    template_path = Path(path)
    if not template_path.is_absolute():
        template_path = REPO_ROOT / template_path
    template = template_path.read_text(encoding="utf-8")
    if template.count(PLACEHOLDER) != 1:
        raise ValueError(f"{template_path} must contain {PLACEHOLDER} exactly once")
    return template


def render(template: str, user_text: str) -> str:
    """Insert user text as data. Plain replacement, so braces in the text are inert."""
    return template.replace(PLACEHOLDER, user_text)


def parse_verdict(output: str) -> GuardVerdict:
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    if not lines:
        raise GuardParseError("empty guard output")
    head = lines[0].lower()
    if head == "safe":
        return GuardVerdict(safe=True, raw=output)
    if head == "unsafe":
        categories = [c.strip() for c in lines[1].split(",")] if len(lines) > 1 else []
        if not all(_CATEGORY.match(c) for c in categories):
            raise GuardParseError(f"unexpected category line: {lines[1]!r}")
        return GuardVerdict(safe=False, categories=categories, raw=output)
    raise GuardParseError(f"first line must be 'safe' or 'unsafe', got {lines[0]!r}")

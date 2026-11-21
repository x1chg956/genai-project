"""Exit-gate check (docs/02 Phase 1): no float is used for money.

Every use of `float` in the code is listed here with why it is not money. A new use fails
this test until someone reviews it and adds it, so the grep in the exit gate stays true.
"""

from __future__ import annotations

import re

from auditguard.config import REPO_ROOT

CODE = ("auditguard", "mcp_servers", "data", "eval")
ALLOWED = {
    ("auditguard/tracing.py", "timeout_s: float"),  # seconds
    ("auditguard/config.py", "timeout_s: float"),  # seconds
    ("auditguard/config.py", "temperature: float"),  # model sampling
    ("auditguard/config.py", "score_floor: float"),  # retrieval score
    ("auditguard/prompts.py", "temperature: float"),
    ("auditguard/prompts.py", 'float(meta["temperature"])'),
    ("auditguard/approval/canonical.py", "isinstance(value, float)"),  # refuses floats
    ("auditguard/approval/canonical.py", "money is never a float"),  # docstring
    ("auditguard/schemas/common.py", "isinstance(value, float)"),  # refuses floats
    ("auditguard/schemas/common.py", "never a float"),  # the refusal's message
    ("mcp_servers/regulatory_kb/index.py", "float(raw)"),  # BM25 score, stored as Decimal
    ("data/generator/spec.py", "isinstance(value, float | int)"),  # YAML range -> exact Decimal
    ("data/generator/generate.py", "hours=float("),  # a duration in hours
}
_FLOAT = re.compile(r"\bfloat\b")


def test_float_is_never_used_for_money() -> None:
    found = []
    for root in CODE:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#", 1)[0]
                if not _FLOAT.search(code) or code.strip().startswith(('"', "'")):
                    continue
                if not any(rel == f and snippet in code for f, snippet in ALLOWED):
                    found.append(f"{rel}:{number}: {line.strip()}")
    assert not found, "review these float uses (money must be Decimal):\n" + "\n".join(found)

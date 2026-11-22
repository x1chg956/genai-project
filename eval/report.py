"""Results table for one experiment run (docs/10 §7): `python -m eval.report runs/<exp_id>`."""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

COLUMNS = (
    "case_id",
    "repeat",
    "status",
    "success",
    "recommendation",
    "tool_calls",
    "structured_retries",
    "tokens",
    "cost_usd",
    "latency_s",
    "violations",
)
CHECKS = (
    "disposition",
    "rules",
    "numbers",
    "citations",
    "trajectory",
    "final_state",
    "required_facts",
    "forbidden_claims",
)


def nearest_rank(values: list[Any], pct: int) -> Any:
    ordered = sorted(values)
    return ordered[max(1, -(-pct * len(ordered) // 100)) - 1]


def _cell(row: dict[str, Any], col: str) -> str:
    if col == "tokens":
        return f"{row.get('input_tokens') or 0}+{row.get('output_tokens') or 0}"
    if col == "violations":
        return ",".join(row.get("violations") or []) or "-"
    value = row.get(col)
    return "-" if value is None else str(value)


def summary(rows: list[dict[str, Any]]) -> list[str]:
    ran = [r for r in rows if r.get("checks")]
    scored = [r for r in ran if r.get("scored_checks")]
    latencies = [r["latency_s"] for r in ran if r.get("latency_s") is not None]
    costs = [Decimal(str(r.get("cost_usd") or "0")) for r in ran]
    errors = [r for r in rows if r.get("errors")]
    lines = [
        f"task success: {sum(1 for r in scored if r['success'])}/{len(scored)} scored runs "
        f"({len(rows) - len(scored)} unscored or not run)",
        f"policy violations: {sum(len(r.get('violations') or []) for r in rows)} "
        f"in {sum(1 for r in rows if r.get('violations'))} runs",
        f"guardrail blocks: {sum(r.get('guard_blocks') or 0 for r in ran)}",
        f"cost per case: {(sum(costs, Decimal(0)) / len(costs)) if costs else Decimal(0)} USD",
        f"latency: median {statistics.median(latencies) if latencies else '-'} s, "
        f"p95 {nearest_rank(latencies, 95) if latencies else '-'} s",
        f"runs with errors (incl. timeouts, not run): {len(errors)}",
    ]
    for name in ("numbers", "citations"):
        graded = [r for r in ran if r["checks"].get(name, {}).get("status") not in (None, "SKIP")]
        passed = sum(1 for r in graded if r["checks"][name]["status"] == "PASS")
        lines.append(f"{name}-match: {passed}/{len(graded)} drafts")
    by_repeat: dict[int, list[bool]] = defaultdict(list)
    for r in scored:
        by_repeat[r.get("repeat", 1)].append(bool(r["success"]))
    if len(by_repeat) > 1:
        rates = [sum(v) / len(v) for v in by_repeat.values()]
        lines.append(f"success spread over {len(rates)} repeats: {min(rates):.2f}-{max(rates):.2f}")
    return lines


def render(rows: list[dict[str, Any]]) -> str:
    head = [*COLUMNS, *CHECKS]
    lines = [" | ".join(head), " | ".join("---" for _ in head)]
    for r in rows:
        cells = [_cell(r, c) for c in COLUMNS]
        cells += [r.get("checks", {}).get(c, {}).get("status", "-") for c in CHECKS]
        lines.append(" | ".join(cells))
    return "\n".join([*lines, "", *summary(rows)])


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m eval.report runs/<exp_id>", file=sys.stderr)
        return 2
    path = Path(args[0]) / "results.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    print(render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())

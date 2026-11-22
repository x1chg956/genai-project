"""Clause-aware chunking (docs/07 §Ingestion): one chunk per numbered clause, long clauses
split at their sub-clauses, clause numbers kept as `clause_ref` ("8", "8(2)", "8(2)(a)").

Recognised headings, at the start of a line:
- a clause: "8." or "8A." or "Rule 8." / "Section 12." / "Paragraph 65."
- a sub-clause: "(2)" then "(a)" then "(i)"; roman numerals only below a letter.
Text before the first clause is kept as clause "preamble". Tables arrive as text lines
(the parser keeps cells in order) and stay inside their clause.
Pure: text in, chunks out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CLAUSE = re.compile(r"^(?:(?:rule|section|paragraph|para)\s+)?(\d+[A-Z]?)\.\s+(.*)$", re.I)
_SUB = re.compile(r"^\(([0-9]+|[a-z]{1,2}|[ivx]+)\)\s*(.*)$")
_ROMAN = re.compile(r"^[ivx]+$")


@dataclass(frozen=True)
class Chunk:
    clause_ref: str
    text: str


def _level(label: str, refs: dict[int, str], last_level: int) -> int:
    """1 for (2), 2 for (a), 3 for (i). "(i)" right after "(h)" is the letter i."""
    if label.isdigit():
        return 1
    roman = _ROMAN.match(label) and refs[2]
    if roman and (last_level == 3 or (label == "i" and refs[2] != "(h)")):
        return 3
    return 2


def _split(clause: str, lines: list[str], max_chars: int) -> list[Chunk]:
    text = " ".join(lines).strip()
    if len(text) <= max_chars:
        return [Chunk(clause, text)] if text else []
    out: list[Chunk] = []
    refs = {1: "", 2: "", 3: ""}
    current, buf, last_level = clause, [], 0
    for line in lines:
        m = _SUB.match(line)
        if not m:
            buf.append(line)
            continue
        label = m.group(1)
        level = _level(label, refs, last_level)
        if buf:
            out.append(Chunk(current, " ".join(buf).strip()))
        refs[level] = f"({label})"
        for deeper in range(level + 1, 4):
            refs[deeper] = ""
        current = clause + "".join(refs[i] for i in range(1, level + 1))
        buf, last_level = [line], level
    if buf:
        out.append(Chunk(current, " ".join(buf).strip()))
    return [c for c in out if c.text]


def chunk_clauses(text: str, max_chars: int = 1500) -> list[Chunk]:
    clauses: list[tuple[str, list[str]]] = [("preamble", [])]
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        m = _CLAUSE.match(line)
        if m:
            clauses.append((m.group(1), [line]))
        else:
            clauses[-1][1].append(line)
    out: list[Chunk] = []
    seen: dict[str, int] = {}
    for ref, lines in clauses:
        for c in _split(ref, lines, max_chars):
            n = seen.get(c.clause_ref, 0)
            seen[c.clause_ref] = n + 1
            # a repeated ref (e.g. two schedules both numbered 1) gets a stable suffix
            out.append(c if n == 0 else Chunk(f"{c.clause_ref}#{n + 1}", c.text))
    return out

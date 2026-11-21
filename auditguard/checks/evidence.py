"""Code checks on a draft: every cited ID resolves, every number came from a tool (rule 1).

Pure functions over the tool records of one run. The agent node runs them before accepting
an answer, and the Phase 1 mini evaluation runs them again on the final draft.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from decimal import Decimal, InvalidOperation
from typing import Any

from auditguard.hooks.tools import ToolRecord, is_error
from auditguard.schemas import DraftReport

# Identifier-like tokens (TXN-SYN-0000001, ALR-SYN-0001, R04, ACCT-SYN-000001, ledger refs)
# carry digits that are not quantities; they are removed before numbers are read.
_ID = re.compile(r"\b(?:[A-Z][A-Z0-9]*-)+[A-Z0-9]*\d[A-Z0-9-]*\b|\bR\d{2}\b|ledger:v1:\S+")
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, bool):
        return
    elif isinstance(value, int | Decimal):
        yield str(value)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


def numbers_in(text: str) -> set[Decimal]:
    """Quantities written in text (amounts, counts, dates' parts), IDs excluded."""
    found: set[Decimal] = set()
    for raw in _NUMBER.findall(_ID.sub(" ", text)):
        try:
            found.add(Decimal(raw.replace(",", "")).normalize())
        except InvalidOperation:
            continue
    return found


def tool_numbers(records: Iterable[ToolRecord]) -> set[Decimal]:
    found: set[Decimal] = set()
    for record in records:
        if not is_error(record.result):
            for text in _strings(record.result):
                found |= numbers_in(text)
    return found


def known_ids(records: Iterable[ToolRecord]) -> set[str]:
    """Evidence IDs a draft may cite: ledger refs, transactions, rules and passages the tools
    returned."""
    ids: set[str] = set()
    for record in records:
        result = record.result
        if is_error(result):
            continue
        if isinstance(result.get("ledger_ref"), str):
            ids.add(result["ledger_ref"])
        for txn in result.get("transactions", []):
            ids.add(txn["txn_id"])
        for rule in result.get("results", []):
            ids.add(rule["rule_id"])
            ids.update(rule.get("supporting_txn_ids", []))
        for passage in result.get("passages", []):
            ids.add(passage["passage_id"])
        if "passage_id" in result:
            ids.add(result["passage_id"])
        if "rule_id" in result:
            ids.add(result["rule_id"])
    return ids


def unresolved_ids(draft: DraftReport, known: set[str]) -> list[str]:
    return sorted(draft.cited_ids() - known)


def unsupported_numbers(draft: DraftReport, allowed: set[Decimal]) -> list[str]:
    """Numbers in the draft that no tool returned, as written (e.g. '8,891.53')."""
    bad: list[str] = []
    for sentence in draft.sections.sentences():
        for number in sorted(numbers_in(sentence.text) - allowed):
            bad.append(format(number, "f"))
    return sorted(set(bad))


_FIRED_WORDS = re.compile(r"\b(fired|fires|triggered|triggers|was met|matched|flagged)\b", re.I)
_NEGATION = re.compile(r"\b(not|no|did not|didn't|never|neither|nor)\b", re.I)


def contradicted_rule_claims(draft: DraftReport, fired: dict[str, bool]) -> list[str]:
    """Sentences that say a rule fired when the rule engine says it did not.

    Deliberately narrow: a sentence citing a not-fired rule may not use a "fired" word
    unless it also negates. The semantic check of every claim is the critic's job (Phase 4).
    """
    bad: list[str] = []
    for sentence in draft.sections.sentences():
        not_fired = [rid for rid in sentence.evidence_ids if fired.get(rid) is False]
        if not_fired and _FIRED_WORDS.search(sentence.text) and not _NEGATION.search(sentence.text):
            bad.append(
                f"{', '.join(not_fired)} did not fire, but the draft says: {sentence.text!r}"
            )
    return bad

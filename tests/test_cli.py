"""CLI: the edit format round-trips, and scripted answers drive a case to a receipt."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from auditguard.cli import draft_to_text, run_case, text_to_draft
from auditguard.schemas import DraftReport
from tests.graph.world import ANALYST, OFFICER, World

DRAFT = DraftReport.model_validate(
    {
        "version": 3,
        "sections": {
            "introduction": [{"text": "Alert reviewed.", "material": False}],
            "grounds": [
                {"text": "R04 indicator observed.", "evidence_ids": ["R04", "TXN-SYN-0000001"]}
            ],
        },
    }
)


def test_edit_format_round_trips() -> None:
    assert DraftReport.model_validate(text_to_draft(draft_to_text(DRAFT), 3)) == DRAFT


def test_edit_format_refuses_unknown_sections() -> None:
    with pytest.raises(ValueError, match="unknown section"):
        text_to_draft("## summary\nhello\n", 1)


def test_material_line_without_ids_fails_validation() -> None:
    with pytest.raises(ValueError):
        DraftReport.model_validate(text_to_draft("## body\n[] The account moved money.\n", 1))


def scripted(answers: list[str]) -> Any:
    queue = list(answers)

    def ask(prompt: str) -> str:
        return queue.pop(0)

    return ask


def run(
    world: World, answers: list[str], *, thread: str = "T1", edit: Any = lambda text: text
) -> tuple[dict[str, Any], list[str]]:
    lines: list[str] = []
    state = asyncio.run(
        run_case(
            deps=world.deps(),
            alert=world.start()["alert"],
            analyst=ANALYST,
            thread_id=thread,
            ask=scripted(answers),
            edit=edit,
            out=lines.append,
            traced=False,
        )
    )
    return state, lines


def test_scripted_run_reaches_a_receipt_and_shows_the_hash(tmp_path: Path) -> None:
    state, lines = run(World(tmp_path), ["file", "approve", OFFICER])
    assert state["status"] == "SUBMITTED"
    text = "\n".join(lines)
    assert f"SHA-256 of the exact payload: {state['payload'].sha256}" in text
    assert "[ingest_validate] status=IN_PROGRESS" in text and "tool evaluate_rules: ok" in text


def test_edit_in_the_editor_gives_version_2(tmp_path: Path) -> None:
    def edit(text: str) -> str:
        return text.replace("Referred for review.", "Referred to the Principal Officer.")

    state, lines = run(World(tmp_path), ["file", "edit", "approve", OFFICER], edit=edit)
    assert state["status"] == "SUBMITTED" and state["payload"].report_version == 2
    assert any("Changes from the previous version" in line for line in lines)


def test_self_approval_is_asked_again(tmp_path: Path) -> None:
    state, lines = run(World(tmp_path), ["file", "approve", ANALYST, "approve", OFFICER])
    assert state["status"] == "SUBMITTED"
    assert any("self-approval refused" in line for line in lines)


def test_stopped_run_resumes_on_the_same_thread(tmp_path: Path) -> None:
    world = World(tmp_path)

    def stop(prompt: str) -> str:
        raise EOFError  # the analyst closes the terminal at the decision gate

    with pytest.raises(EOFError):
        asyncio.run(
            run_case(
                deps=world.deps(),
                alert=world.start()["alert"],
                analyst=ANALYST,
                thread_id="T9",
                ask=stop,
                out=lambda s: None,
                traced=False,
            )
        )
    reads = world.repo.reads
    state, lines = run(world, ["file", "approve", OFFICER], thread="T9")
    assert lines[0] == "resuming thread T9 at decision_gate"
    assert state["status"] == "SUBMITTED" and world.repo.reads == reads

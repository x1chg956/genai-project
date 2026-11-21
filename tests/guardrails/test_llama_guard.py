from __future__ import annotations

from pathlib import Path

import pytest

from auditguard.guardrails.llama_guard import (
    PLACEHOLDER,
    GuardParseError,
    load_template,
    parse_verdict,
    render,
)

TEMPLATE_PATH = "prompts/guard_llama_guard_3.txt"


def test_template_has_one_placeholder_and_official_framing() -> None:
    template = load_template(TEMPLATE_PATH)
    assert template.count(PLACEHOLDER) == 1
    assert template.startswith("<|begin_of_text|><|start_header_id|>user<|end_header_id|>")
    assert template.endswith("<|start_header_id|>assistant<|end_header_id|>\n\n")
    assert "S13: Elections." in template


def test_render_inserts_text_verbatim() -> None:
    template = load_template(TEMPLATE_PATH)
    text = "memo {with} braces and {{user_text}} inside"
    rendered = render(template, text)
    assert f"User: {text}\n" in rendered


def test_template_without_placeholder_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.txt"
    bad.write_text("no placeholder here")
    with pytest.raises(ValueError, match="exactly once"):
        load_template(bad)


def test_parse_safe() -> None:
    verdict = parse_verdict("safe")
    assert verdict.safe is True
    assert verdict.categories == []


def test_parse_unsafe_with_categories() -> None:
    verdict = parse_verdict("unsafe\nS2,S9")
    assert verdict.safe is False
    assert verdict.categories == ["S2", "S9"]


def test_parse_tolerates_surrounding_whitespace() -> None:
    assert parse_verdict("\n unsafe \n S1 \n").categories == ["S1"]


@pytest.mark.parametrize("output", ["", "maybe", "unsafe\nnot-a-category"])
def test_parse_rejects_unexpected_output(output: str) -> None:
    with pytest.raises(GuardParseError):
        parse_verdict(output)

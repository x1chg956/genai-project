---
description: Check the codebase against AuditGuard's three rules and hard constraints
---
Audit the repository against CLAUDE.md:

- Any LLM call whose output becomes a number, count, date difference or threshold in a report (rule 1).
- Any `float` used for money; any naive datetime.
- Any prompt that inserts untrusted text without a <data> block, or any path where retrieved text can trigger a tool call (rule 2).
- Any call to `submit_approved_payload` outside the `submit` code node, or a submit path that skips hash verification (rule 3).
- Hard-coded model IDs, thresholds or prompt text outside configs/ and prompts/.
- Any code that connects to the groundtruth database outside eval/ and data/generator/.
- Any reference to HELDOUT_DIR outside eval/harness.py.

Report each finding with file:line and a one-line fix, then ask before fixing.

# 10 Evaluation

## 1. Golden set (written by role 1; Claude Code builds the tooling)

| Category | Dev | Held-out |
|---|---|---|
| Suspicious (R01–R08, variants, evasive) | 9 | 17 |
| Benign look-alikes | 7 | 13 |
| Edge, ambiguous, data-quality | 4 | 8 |
| Repeat-subject follow-ups | 4 | 10 |
| **Total (72)** | **24** | **48** |

- Dev cases: `eval/cases/dev/*.jsonl` in the repo.
- Held-out cases: a directory **outside the repo** given by `HELDOUT_DIR`; never mounted into agent or MCP containers. Claude Code never reads it.
- ≥15 held-out cases are drill-down cases (cannot be solved without follow-up evidence).
- Variants of a scenario and repeat-subject pairs stay in one split.
- Phase 1 uses 6 dev case scaffolds.

## 2. Case format (one JSON object per line)

```json
{
  "case_id": "DEV-001", "split": "dev", "scenario_family": "rapid_pass_through", "variant": "standard",
  "seed": 1234, "alert_id": "ALR-SYN-0001",
  "expected_rule_outputs": {"must_fire": ["R04", "R07"], "must_not_fire": ["R01"]},
  "expected_disposition": "escalate_for_review",
  "expected_hypotheses": {"confirmed": ["mule_pass_through"], "refuted": ["payroll"]},
  "expected_evidence_ids": ["TXN-SYN-0000123"], "expected_passage_ids": ["PML-RULES-2005:8(2):…"],
  "expected_trajectory": {"required_tool_classes": ["rules", "drilldown", "retrieval"], "forbidden_tools": ["submit_approved_payload"], "max_tool_calls": 20},
  "expected_replan": {"required": false, "reason": null},
  "required_facts": ["inflows from 23 distinct senders"], "forbidden_claims": ["customer is a criminal"],
  "history_links": [], "attack_label": null,
  "gate_script": {"decision": "file", "approval": "approve"}
}
```

`gate_script` tells the harness how to answer each `interrupt()` so runs are unattended; approval uses a separate approver identity. A case without a `gate_script` is answered `needs_info` (nothing is filed by default). Null expectations are scaffolds: their checkers report SKIP, never a guess.

**Disposition** is scored on the agent's `recommendation` (`escalate_for_review | no_action | insufficient_data`, the same vocabulary as `expected_disposition`), not on the scripted human decision, which only exercises the gates.

**Phase 1 mini evaluation** (`make mini-eval`, `eval/mini.py`): runs `eval/cases/dev/phase1.jsonl` through the C1 graph live and writes `runs/mini-<time>/` with one run record per case (trace IDs, tool records, stats, checks, config and prompt hashes, model IDs) and `results.jsonl`. Its table has, per case: status, recommendation, tool calls, invalid tool calls, structured retries, tokens, latency and the six checks, then numbers-match and citations-match over the drafts.

## 3. Harness (`eval/harness.py`)

```bash
python -m eval.harness --config configs/experiments/c2_team.yaml --split dev --repeats 1 --out runs/
```

- Loads the experiment config (which graph: c0 | c1 | c2; which toggles), the cases, and runs each with a fresh `run_id`.
- Answers interrupts from `gate_script`.
- Writes `runs/<exp_id>/<run_id>.json` (state snapshot, trace ID, tool calls, tokens, cost, latency, errors, lessons used, config hash, prompt hashes, model IDs, seed) and `results.jsonl`.
- `python -m eval.report runs/<exp_id>` prints the results table.
- Failed and timed-out runs are recorded, never dropped.

## 4. Checkers (`eval/checkers/`), code first

| Checker | Pass when |
|---|---|
| disposition | final decision matches `expected_disposition` |
| rules | every `must_fire` fired and no `must_not_fire` fired |
| numbers | every number in the draft equals a tool output in the run record |
| citations | every evidence ID resolves at its source; zero fabricated |
| required_facts / forbidden_claims | substring or normalised match; judge for paraphrase |
| trajectory | required tool classes called; no forbidden tool; ≤ max calls |
| replan | replan happened iff `expected_replan.required` |
| final_state | portal has a receipt iff approved escalation |
| pii | no raw identifier patterns in trace, logs, prompts |

## 5. Judge (`eval/judge.py`)

- Model from another family than the agents (config `models.judge`; the agents run Qwen, so the judge must not), temperature 0, six-slot prompt with a rubric per criterion: claim_support (per sentence), tone_neutrality, completeness.
- Never sees expected outcomes beyond what the criterion needs.
- Calibration: `eval/judge_calibration/labels.jsonl` with ≥30 drafts labelled independently by two teammates; `python -m eval.calibrate` reports Cohen's kappa overall and per criterion. Target κ ≥ 0.6.

## 6. Experiments (one YAML per arm in `configs/experiments/`)

| Exp | Arms (files) |
|---|---|
| E1 | `c0_fixed.yaml`, `c1_single.yaml`, `c2_team.yaml` (same model, tools, budget) |
| E2 | `e2_defences_off.yaml`, `e2_defences_on.yaml` (red-team set + all clean held-out cases) |
| E3 | `e3_no_playbook.yaml`, `e3_playbook.yaml` |
| E4a | `e4a_llm_numbers.yaml` (LLM computes indicators from raw ledger, for ablation only), `e4a_engine.yaml` |
| E4b | `e4b_critic_off.yaml`, `e4b_critic_on.yaml` |
| E4c | `e4c_b5.yaml`, `e4c_b10.yaml`, `e4c_b20.yaml`, `e4c_no_drilldown.yaml` |
| E5 | retrieval staircase (see `07_RETRIEVAL.md`) |
| E6 | `e6_memory_off.yaml`, `e6_memory_on.yaml` |

Experiment configs override `configs/base.yaml`; they never change code paths except through declared toggles.

## 7. Results table (every configuration)

task-success counts · policy violations · attack success · false blocks · cost per case · median and p95 latency · errors including timeouts · spread over 3 repeats (held-out). If anything changes after viewing held-out results, those cases are moved to dev and the change is logged.

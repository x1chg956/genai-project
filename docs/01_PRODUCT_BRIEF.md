# 01 Product brief

## Problem

An AML analyst receives a transaction-monitoring alert and must assemble the transaction evidence, test it against rules and regulatory text, decide between filing and not filing, and write a narrative a reviewer can defend. Missed indicators and unsupported narratives both cost. AuditGuard does the evidence work and drafts the report; people make the decisions.

## Users

| Role | Can do | Cannot do |
|---|---|---|
| Analyst (requester) | Triage alerts, run investigations, record file or no-file, edit drafts | Approve or submit |
| Principal Officer (approver) | Approve, edit, return or reject the exact payload | Approve a case they requested |
| Second-line reviewer | Read cases, sample no-file closures, approve Reflexion lessons | Approve reports |
| Admin | Manage rule packs, corpus versions, users | See de-pseudonymised data by default |

## What one run does

Input: one synthetic alert (`alert_id`, `account_ref`, `scenario_code`, `detected_at`).
Output: either a no-file closure with a reason code, or an approved STR-style payload submitted once to the mock portal, with a receipt.

Steps: ingest and validate the ledger → run rules (code) → form and test hypotheses, including benign ones → retrieve supporting regulatory passages and typologies → analyst decides file or no-file → draft a narrative where every sentence cites evidence IDs → critic checks → output guardrails → Principal Officer approves the exact payload → code submits.

## Signature challenges (where the technical marks are)

- **A. Auditable numbers and narrative.** Every number comes from tested rule code; every sentence carries evidence IDs that code resolves.
- **B. Hypothesis-driven investigation under a budget.** The agent ranks hypotheses, spends a tool budget on drill-down, and replans when evidence refutes a hypothesis.

## Scope

- India only (MUST). INR only. Indian commercial bank.
- Rules 01–08 (MUST), 09–12 (SHOULD).
- Out of scope: real filings, live sanctions screening, account freezes, customer contact, CTR/CBWTR/CCR/NTR filings, US profile (COULD, only after everything else).

## Limits to state everywhere

Synthetic data only; thresholds are project heuristics unless a source is tagged; mock portal; not validated on real alerts; not legal advice. Results measure rule coverage and system behaviour, not real-world detection.

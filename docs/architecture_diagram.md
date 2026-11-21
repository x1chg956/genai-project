# AuditGuard architecture diagrams

Mermaid source for the system diagram (Stage 2 target, `03_ARCHITECTURE.md` §1) and the C1 graph being built in Phase 1 (`03_ARCHITECTURE.md` §2). Colours follow the proposal: purple = LLM, coral = control and the only write path, grey = plain code, blue = data stores; the dashed store is ground truth, which no agent or MCP server can reach.

## 1. System (Stage 2 target)

```mermaid
flowchart TB
  analyst(["Analyst · requester"])
  po(["Principal Officer · approver"])

  subgraph SERVE["Serving"]
    ui["Chainlit UI<br/>triage · plan · evidence · approval"]
    api["FastAPI<br/>SSE stream · role tokens · case threads"]
  end

  gin["Input guardrails<br/>schema · pseudonymise · Prompt Guard 2 · Llama Guard · scope"]

  subgraph ORCH["LangGraph · typed CaseState · Postgres checkpointer"]
    plan["Supervisor + planner<br/>plan v1…vN · route · replan"]
    ingest["ingest_validate<br/>coverage · duplicates · reversals"]
    inv["Investigator · ReAct<br/>rules · drill-down · hypotheses"]
    res["Regulatory Researcher · ReAct<br/>fan-out per fired rule"]
    asm["assemble_evidence"]
    dgate{{"decision_gate · interrupt()<br/>file · no-file · needs info"}}
    draft["Drafter<br/>every sentence cites evidence IDs"]
    critic["Evidence Critic<br/>code checks, then judge · ≤ 2 revisions"]
    gout["Output guardrails<br/>provenance · PII · neutral wording · tipping-off"]
    build["build_payload<br/>canonical JSON + SHA-256"]
    agate{{"approval_gate · interrupt()<br/>exact payload + hash"}}
    submit["submit<br/>re-hash · verify approval · idempotency key"]
    memup["memory_update"]
    closed(["CLOSED_NO_FILE"])
  end

  hooks["Hooks around every tool call<br/>pre: identity · allow-list · case scope · budget<br/>post: scrub · log · cost · metrics"]

  subgraph MCP["FastMCP servers · stdio in dev, HTTP in Docker"]
    tx["transactions · R<br/>ledger · KYC · flows · prior reports"]
    aml["aml_engine · C<br/>rules R01–R08 as tested code"]
    kb["regulatory_kb · R<br/>BM25 + bge + RRF + reranker"]
    portal["filing_portal · W<br/>the only write"]
  end

  subgraph DATA["Data"]
    pg[("Postgres auditguard<br/>ledger · cases · approvals · audit · checkpoints · memory")]
    qd[("Qdrant<br/>passage vectors")]
    sq[("Portal SQLite<br/>drafts · submissions · receipts")]
    gt[("Postgres groundtruth<br/>labels · no agent or MCP access")]
  end

  subgraph MODELS["oMLX on the host :8888 · only via auditguard/llm.py"]
    qwen["Qwen3.5-4B-4bit<br/>supervisor · specialists · decomposer"]
    gemma["gemma-3-1b-it-4bit<br/>judge"]
    lguard["Llama-Guard-3-1B<br/>guard · raw completions"]
  end

  subgraph OPS["Operate and measure"]
    gen["Synthetic ledger generator<br/>seeded"]
    harness["Eval harness<br/>checkers · judge · E1–E6"]
    reflex["Reflexion · dev cases only<br/>human-reviewed lessons"]
    obs["Traces · Prometheus · Grafana · CI"]
  end

  analyst --> ui
  po --> ui
  ui --> api --> gin --> plan
  plan --> ingest --> inv --> res --> asm --> dgate
  inv -. refuted or data gap .-> plan
  dgate -- file --> draft --> critic
  critic -- fail --> draft
  critic -- pass --> gout --> build --> agate
  dgate -- no-file --> closed
  agate -- edit voids approval --> critic
  agate -- approve --> submit --> memup
  analyst -. decides .-> dgate
  po -. approves exact payload .-> agate

  ingest & inv & res & critic --> hooks
  hooks --> tx & aml & kb
  build -- prepare_payload --> portal
  submit == submit_approved_payload ==> portal

  tx --> pg
  aml --> pg
  kb --> qd
  portal --> sq

  plan & inv & res & draft -.-> qwen
  critic -.-> gemma
  gin -.-> lguard

  gen --> pg
  gen --> gt
  harness --> gt
  harness -. runs cases .-> plan
  reflex -. accepted lessons .-> pg

  classDef llm fill:#ece6fb,stroke:#6a4bd1,color:#1d1533
  classDef control fill:#fde3da,stroke:#cf4a24,color:#3a1307
  classDef code fill:#eef0f3,stroke:#6b7480,color:#1b1f24
  classDef store fill:#e2edf9,stroke:#2e6db3,color:#0d2440
  classDef sealed fill:#f4f4f4,stroke:#8a8a8a,stroke-dasharray:5 3,color:#2a2a2a
  classDef person fill:#ffffff,stroke:#2b2b2b,color:#111111

  class plan,inv,res,draft,critic,qwen,gemma llm
  class gin,gout,dgate,agate,hooks,submit,portal,lguard control
  class ui,api,ingest,asm,build,memup,closed,tx,aml,kb,gen,harness,reflex,obs code
  class pg,qd,sq store
  class gt sealed
  class analyst,po person
```

## 2. C1 single-agent graph (Phase 1, being built)

```mermaid
flowchart LR
  start(["START"]) --> ingest["ingest_validate<br/>LedgerSlice + DataQualityReport"]
  ingest --> agent["single_agent · ReAct<br/>all R and C tools · no write tool<br/>returns SingleAgentResult"]
  agent --> dgate{{"decision_gate<br/>interrupt()"}}
  dgate -- no-file --> nofile["close_no_file"] --> done1(["END · CLOSED_NO_FILE"])
  dgate -- file --> build["build_payload<br/>canonical JSON + SHA-256"]
  build --> agate{{"approval_gate<br/>interrupt()"}}
  agate -- approve --> submit["submit<br/>re-hash · verify approval"]
  submit == submit_approved_payload ==> portal["filing_portal · W"]
  portal --> done2(["END · receipt"])
  agate -- edit · new hash, old approval void --> build
  agate -- reject --> done3(["END · REJECTED"])

  classDef llm fill:#ece6fb,stroke:#6a4bd1,color:#1d1533
  classDef control fill:#fde3da,stroke:#cf4a24,color:#3a1307
  classDef code fill:#eef0f3,stroke:#6b7480,color:#1b1f24
  class agent llm
  class dgate,agate,submit,portal control
  class ingest,build,nofile code
```

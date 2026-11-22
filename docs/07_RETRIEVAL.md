# 07 Retrieval

## Corpus manifest (`corpus/manifest.yaml`)

Corpus files are **fetched by script**, not committed, unless redistribution is clearly allowed.

```yaml
- doc_id: RBI-KYC-CB-2025
  title: Reserve Bank of India (Commercial Banks - Know Your Customer) Directions, 2025
  issuer: RBI
  reference: RBI/DOR/2025-26/169
  version: "2025-12-29"
  published: 2025-11-28
  effective_from: <confirm with domain reviewer>
  superseded_by: null
  url: <official URL>
  licence_note: <check>
  sha256: <filled by fetch script>
- doc_id: RBI-KYC-MD-2016
  title: Master Direction - KYC, 2016 (superseded; kept to test version filtering)
  superseded_by: RBI-KYC-CB-2025
  ...
- doc_id: PML-RULES-2005
- doc_id: PMLA-2002-S12
- doc_id: FIU-IND-STR-GUIDANCE
- doc_id: TYPOLOGY-*          # public typology material only (FATF reports; FIU-IND material confirmed public)
```

Phase 1 uses two documents with a few clauses each. The domain reviewer signs the manifest.

## Ingestion (`scripts/ingest.py`)

1. Fetch each URL, verify or record `sha256`, store under `corpus/raw/` (git-ignored).
2. Parse PDF or HTML to text, keeping clause numbers (e.g. "Rule 8(2)").
3. Clause-aware chunking: one chunk per numbered clause; split long clauses at sub-clauses; keep tables as text tables.
4. Passage ID: `{doc_id}:{clause_ref}:{version}`, stable across runs.
5. Metadata per chunk: doc_id, title, issuer, version, published, effective_from, superseded_by, clause_ref, kind (`regulation` | `typology`).
6. Index: BM25 (`rank-bm25`, pickled per corpus version) and dense vectors (`BAAI/bge-small-en-v1.5`, CPU) in Qdrant collection `passages_<corpus_version>`.

## Search (`regulatory_kb.search_passages`)

```mermaid
flowchart TB
  q["query + as_of_date"] --> dec["decompose<br/>Phase 2 · LLM, temperature 0<br/>≤ 3 sub-queries"]
  dec --> sq1["sub-query 1"] & sq2["sub-query 2"] & sq3["sub-query 3"]
  subgraph PER["per sub-query"]
    bm["BM25 top 20<br/>rank-bm25"]
    vec["vector top 20<br/>bge-small · Qdrant"]
  end
  sq1 & sq2 & sq3 --> bm & vec
  bm & vec --> rrf["Reciprocal Rank Fusion<br/>k = 60"]
  rrf --> filt["version filter<br/>effective_from ≤ as_of_date<br/>and not superseded by as_of_date"]
  filt --> rr["cross-encoder rerank<br/>ms-marco-MiniLM-L-6-v2"]
  rr --> topk["top k · default 5"]
  topk --> floor{"score ≥ score_floor?"}
  floor -- yes --> ok(["status ok · passages with IDs"])
  floor -- "none left" --> ns(["status no_support · []"])

  classDef llm fill:#ece6fb,stroke:#6a4bd1,color:#1d1533
  classDef code fill:#eef0f3,stroke:#6b7480,color:#1b1f24
  class dec llm
  class bm,vec,rrf,filt,rr,topk,floor code
```

Phase 1 runs only the BM25 branch, the version filter and the score floor (no decomposition, vectors, fusion or reranker). Each later step is one row of the Recall@5 staircase (E5), so its effect is measured on its own.

Built (Phase 2 task 4), `mcp_servers/regulatory_kb/hybrid.py`:
- **Filter first.** Kind, version in force on `as_of_date` and `doc_filter` are applied before any ranking, in both branches, so a superseded clause can never outrank the one in force (stricter than the diagram, which filters after fusion).
- **Queries.** The agent's query is always kept, plus at most 3 sub-queries from the decomposer (role `decomposer`, `prompts/decomposer.md`). A failed decomposition falls back to the query.
- **Branches.** BM25 top `k_bm25` with score > 0; vectors top `k_vector` with cosine ≥ `retrieval.vector_floor` (0.55, a heuristic to calibrate in E5). Embeddings come from `fastembed` (ONNX, CPU) running `BAAI/bge-small-en-v1.5`, into the Qdrant collection `passages_<first 12 hex of the passages file's SHA-256>`, built on first use.
- **Fusion and scores.** One list keeps its own scores; several are fused by RRF. The reranker is fastembed's ONNX export of `cross-encoder/ms-marco-MiniLM-L-6-v2`. It scores the fused top `rerank_candidates`, and the score is the sigmoid of its logit, floored at `score_floor` (0.2). With BM25 alone, the Phase 1 floor on BM25 scores still applies.
- **BM25 index.** Rebuilt from the passages at server start (milliseconds) instead of pickled.
- **Other tools.** `search_typologies` searches `kind: typology` passages, with no version filter. `list_document_versions` lists a document's versions, oldest first.
- **Corpus.** `corpus/manifest.yaml` is a draft with official URLs found on 2026-11-22, unsigned. `scripts/fetch_corpus.py` refuses it until the domain reviewer signs. `scripts/ingest.py` parses HTML and PDF (pypdf), chunks by clause (`auditguard/corpus/chunk.py`) and writes passages plus vectors.
- **Staircase.** `eval/retrieval.py` (`make retrieval-eval`) runs the four arms on role 1's queries.

Version preference is a metadata filter, not a prompt instruction. Retrieved text is returned inside a `Passage` model and wrapped in a data block by the caller.

## Labelled queries (`eval/retrieval/queries.jsonl`, labelled by role 1)

40 queries in four types (rule provenance, typology, procedure, definition), 10 each:

```json
{"qid": "Q001", "type": "rule_provenance", "query": "time limit to furnish a suspicious transaction report", "as_of": "2026-06-01", "relevant": ["PML-RULES-2005:8(2):…"]}
```

## E5 staircase (`make retrieval-eval`)

| Arm | Config |
|---|---|
| vector only | `retrieval: {bm25: false, rerank: false, decompose: false}` |
| + BM25 with RRF | `bm25: true` |
| + reranker | `rerank: true` |
| + decomposition | `decompose: true` |

Report Recall@5, MRR and "in-force version ranked first" for each arm. The Recall@5 of the agreed baseline becomes a CI gate.

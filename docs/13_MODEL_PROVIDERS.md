# 13 Model providers (local by default, configurable)

Every model call goes through `auditguard/llm.py`. The code knows only **roles** (`supervisor`, `specialist`, `judge`, `guard`, `decomposer`). Which server and model serve each role is decided in config, so switching between the local server and a hosted provider never touches code.

## 1. Default: local OpenAI-compatible server

The default endpoint is the local model server at **`http://127.0.0.1:8888/v1`**, which speaks the OpenAI Chat Completions API. It is called through `langchain-openai` `ChatOpenAI(base_url=..., api_key=..., model=...)`.

## 1a. Verified local server (21 Nov 2026)

| Item | Result |
|---|---|
| Server | oMLX, OpenAI-compatible, at `http://127.0.0.1:8888/v1`; needs a bearer key (401 without one) |
| Model | `Qwen3.5-9B-OptiQ-4bit` since 21 Nov 2026 (`max_model_len` 262144; llm-check: tools, strict json_schema, thinking off and a 54k-token prompt all pass). Phase 0 was verified on `Qwen3.5-4B-4bit` |
| Chat | works |
| Tool calling | works: returns proper `tool_calls`, reasoning kept in a separate `reasoning_content` field |
| Structured output | `response_format: json_schema` (strict) returns valid JSON |
| Thinking mode | on by default and leaks into `content` on plain chat. Turn it off per request with `chat_template_kwargs: {enable_thinking: false}`; the `/no_think` tag does **not** work on this server |
| Latency | about 0.5–2.8 s per short call |

Config consequences: set `LLM_MODEL=Qwen3.5-9B-OptiQ-4bit`, put the key in `.env` as `LLM_API_KEY` (never in a committed file), and set `extra_body: {chat_template_kwargs: {enable_thinking: false}}` on every local role except where a run deliberately tests thinking. The judge must not be a Qwen model (different family rule).

## 1b. Local judge and guard candidates (tested 21 Nov 2026)

The oMLX server serves the judge and the guard next to the agent model. **Decision (21 Nov 2026): both run locally** (judge = Gemma, guard = Llama Guard 3 1B), so every role runs on the local server and no hosted key is needed. OpenRouter remains a config switch (§2). The judge was first `gemma-3-1b-it-4bit`, which failed the core judge test; it was replaced the same day by **`gemma-4-e2b-it-OptiQ-4bit`** (128k context), which passed it. Passing a probe is not validation: judge calibration (`docs/10` §5, κ ≥ 0.6 on 30 human labels) decides whether it stays.

| Model | Test | Result | Verdict |
|---|---|---|---|
| `gemma-3-1b-it-4bit` (judge candidate) | "Reply with exactly OK" | `OK` followed by multilingual garbage; 150 s cold load | Not usable as judge |
| | Claim "INR 90,000 [TXN-1]" vs evidence "INR 50,000" (strict `json_schema`) | Valid JSON, but `supported: true` with an invented reason | Fails the core judge task |
| `gemma-4-e2b-it-OptiQ-4bit` (judge, current) | "Reply with exactly OK", with and without the `<end_of_turn>` stop | `OK` both ways (~0.2 s warm) | Clean; stop token not needed |
| | 6 claim-vs-evidence pairs, strict `json_schema`: correct amount, wrong amount (INR 90,000 vs 50,000), correct date, wrong date, R07 fired correctly, R07 claimed fired when it did not | **6/6 correct**, ~1 s each, reasons cite the mismatch | **Default judge**; calibration pending |
| | `make llm-check` | chat, json_schema, thinking off, 54k-token prompt: all PASS; no same-family warning | Pass |
| `Llama-Guard-3-1B-chat-mlx-4bit` (guard candidate) via `/chat/completions` | 3 unsafe + 2 safe prompts, plain or with the policy prompt as the message | `safe` for all 5: the server's chat template does not apply Llama Guard's policy format | Unusable over chat |
| | Same 5 prompts via `/completions` with the official Llama Guard 3 raw prompt (special tokens + S1–S13 categories) | 5/5 correct: pipe bomb `unsafe S1`, laundering `unsafe S2`, tipping-off `unsafe S2`, both analyst requests `safe`; ~0.1 s | **Works in `completions_raw` mode** |

Consequences:
- The local judge is a known risk. Mitigations, in order: (1) code checks run before the judge and already catch wrong numbers and unresolved IDs (`docs/06` critic), so the judge only scores claim support and tone; (2) **done in Phase 0:** oMLX does not stop on Gemma's end-of-turn token, so `models.judge.stop: ["<end_of_turn>"]` is set in `configs/base.yaml`; with it, "Reply with exactly OK" returns `OK`. Under a `json_schema` constraint Gemma 1B also needs the expected JSON keys described in the prompt (the Format slot), otherwise it never closes the object and hits `max_tokens`. With both, `make llm-check` passes for the judge (structured output via `json_schema`). This proves the format works, not that the verdicts are right: the wrong-amount test above still stands; (3) if calibration misses κ ≥ 0.6, serve a larger non-Qwen model (e.g. Gemma 3 12B/27B) or switch `JUDGE_PROVIDER=openrouter`. With `gemma-4-e2b-it-OptiQ-4bit` the format workarounds are no longer needed; record "small local judge (Gemma 4 E2B)" as a stated limit until calibration passes.
- A local guard must use `mode: completions_raw`: code builds the official prompt from a template file in `prompts/` and calls `/completions`. Llama Guard 3 has categories S1–S13 (Llama Guard 4 adds S14); the template file pins the category list.
- Prompt-injection detection is Prompt Guard 2's job, not Llama Guard's: a "ignore previous instructions" memo is correctly `safe` for Llama Guard.

## 2. Config (`configs/base.yaml`)

```yaml
providers:
  local:
    base_url: ${LLM_BASE_URL:-http://127.0.0.1:8888/v1}
    api_key: ${LLM_API_KEY}                   # required by the local oMLX server; set in .env only
    timeout_s: 120
    extra_body: {chat_template_kwargs: {enable_thinking: false}}   # oMLX/Qwen: stop thinking text leaking into content
    structured_method: json_schema            # verified on oMLX
    cost_per_1k_tokens: {input: 0, output: 0} # local runs cost nothing; tokens are still counted
  openrouter:
    base_url: https://openrouter.ai/api/v1
    api_key: ${OPENROUTER_API_KEY:-}
    timeout_s: 60
    cost_per_1k_tokens: {}                    # filled from OpenRouter pricing when used

models:
  supervisor: {provider: local, id: ${LLM_MODEL:-Qwen3.5-9B-OptiQ-4bit}, temperature: 0,   max_tokens: 2000}
  specialist: {provider: local, id: ${LLM_MODEL:-Qwen3.5-9B-OptiQ-4bit}, temperature: 0.2, max_tokens: 3000}
  decomposer: {provider: local, id: ${LLM_MODEL:-Qwen3.5-9B-OptiQ-4bit}, temperature: 0,   max_tokens: 400}
  judge:      {provider: ${JUDGE_PROVIDER:-local}, id: ${JUDGE_MODEL:-gemma-4-e2b-it-OptiQ-4bit}, temperature: 0, max_tokens: 1500}
  guard:      {provider: ${GUARD_PROVIDER:-local}, id: ${GUARD_MODEL:-Llama-Guard-3-1B-chat-mlx-4bit}, temperature: 0,
               mode: ${GUARD_MODE:-completions_raw}, prompt_template: ${GUARD_PROMPT_TEMPLATE:-prompts/guard_llama_guard_3.txt}}
```

Guard `mode`: `completions_raw` (default, local oMLX: code renders `prompt_template` and calls `/completions`; see §1b) or `chat` (OpenRouter applies the Llama Guard template server-side). The hosted alternative is:

```bash
JUDGE_PROVIDER=openrouter
JUDGE_MODEL=google/gemini-3.1-flash-lite
GUARD_PROVIDER=openrouter
GUARD_MODEL=meta-llama/llama-guard-4-12b
GUARD_MODE=chat
GUARD_PROMPT_TEMPLATE=
OPENROUTER_API_KEY=...
```

- `${VAR:-default}` is expanded by `auditguard/config.py` from the environment (and `.env`).
- `.env.example` (shipped at the repo root) lists `LLM_BASE_URL`, `LLM_BASE_URL_DOCKER`, `LLM_API_KEY`, `LLM_MODEL`, `JUDGE_PROVIDER`, `JUDGE_MODEL`, `GUARD_PROVIDER`, `GUARD_MODEL`, `OPENROUTER_API_KEY` with empty secret values. Copy it to `.env` and fill in the keys.
- Any role can be moved to any provider by editing config or env only. Experiment configs may override `models.*` but must keep the specialist model identical across C0, C1 and C2.

## 3. `auditguard/llm.py`

```python
def get_model(role: str, settings: Settings) -> BaseChatModel:
    m = settings.models[role]; p = settings.providers[m.provider]
    return ChatOpenAI(base_url=p.base_url, api_key=p.api_key, model=m.id,
                      temperature=m.temperature, max_tokens=m.max_tokens,
                      timeout=p.timeout_s, max_retries=2,
                      extra_body=p.extra_body or None,    # e.g. chat_template_kwargs for oMLX
                      callbacks=tracing_callbacks(settings))
```

The guard role in `completions_raw` mode is built by `get_guard(settings)` in the same module (a completions client, not `ChatOpenAI`), so `llm.py` stays the only place model clients are made.

- No other module creates a model client.
- Every run record stores, per role: provider name, base_url, model id, and the model id the server actually reported in its response.
- Cost = tokens × `cost_per_1k_tokens` of the provider (0 for local). Latency and tokens are always recorded.

## 4. Check the local server before use (`make llm-check`)

`scripts/llm_check.py` runs against every configured role and prints a pass/fail table:

1. `GET {base_url}/models` responds, and the configured `id` is listed (if `LLM_MODEL` is unset, print the available IDs and stop).
2. A plain chat completion returns text.
3. **Tool calling**: a request with one `tools` definition returns a valid `tool_calls` entry. The ReAct agents need this.
4. **Structured output**: `with_structured_output(Model)` returns a valid object (try `json_schema`, then `function_calling`, then `json_mode`; record which method works in `models.<role>.structured_method`). Lookup order: `models.<role>.structured_method`, else `providers.<provider>.structured_method`, else `json_schema`.
5. Context length: a prompt of `budgets.case.tokens` size is accepted, or the check reports the limit.
6. Thinking off: a "reply with exactly OK" prompt returns `OK` with no reasoning text in `content`.

If check 3 or 4 fails for the local model, stop and tell the user. Do not work around it by parsing free text.

## 5. Docker

Inside a container, `127.0.0.1` is the container itself, not your machine. In Compose:

```yaml
app:
  environment:
    LLM_BASE_URL: ${LLM_BASE_URL_DOCKER:-http://host.docker.internal:8888/v1}
  extra_hosts:
    - "host.docker.internal:host-gateway"
```

Apply the same to any service that calls a model (app, eval). Outside Docker, the default `http://127.0.0.1:8888/v1` is used.

On a clean machine without oMLX, the Compose profile `llm` runs an OpenAI-compatible model server as a container and sets `LLM_BASE_URL_DOCKER` to it (`11_DEPLOYMENT_AND_CI.md` §2). It must pass `make llm-check` like the local server does.

## 6. Fairness and the course requirements

- **Judge from a different model family.** The course guide requires the judge to come from a different family than the agents. The default judge is the local Gemma model (Google family, not Qwen); OpenRouter is the hosted alternative. `make llm-check` warns when judge and specialist report the same family.
- **Guard.** The default guard is the local `Llama-Guard-3-1B-chat-mlx-4bit`, which works only in `completions_raw` mode (§1b). Record "Llama Guard 3 1B (local) instead of Llama Guard 4" as a stated limit in the evaluation report; Llama Guard 4 on OpenRouter is the hosted alternative. Prompt Guard 2 always runs on CPU in-process.
- **Supervisor stronger than specialists.** If only one local model is available, supervisor and specialists use the same model; record this as a stated limit in the evaluation report.
- Keep model IDs fixed when comparing designs, and record them in every run.
- Local inference makes repeats free, which helps the three-repeat held-out runs; it does not change any other rule.

## 7. Tests

- `test_llm_factory_uses_config`: `get_model` builds clients from config only, with no hard-coded URLs.
- `test_env_override`: setting `LLM_BASE_URL` changes the client's base URL.
- `test_role_provider_switch`: switching `judge.provider` between `local` and `openrouter` changes base URL and key.
- `test_guard_completions_raw` (Phase 6): in `completions_raw` mode the rendered prompt matches the template exactly and `safe` / `unsafe\nS2` parse to typed verdicts.
- `make llm-check` checks the guard with two fixed probes (one clearly unsafe, one benign) and fails if both come back `safe`.
- Graph tests still use a fake chat model; no test calls the local server unless marked `@pytest.mark.live`.

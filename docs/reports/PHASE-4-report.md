# Phase 4 Report — LLM Backends (v0.13.0)

**Plan (6 lines, as built):**
1. `core/llm_client.py` grew the shared client: `openai_chat` (POST
   `<base>/chat/completions` through the SAME `call_with_timeout`
   wrapper as Ollama), `openai_list_models` + `preflight_openai` (the
   `/v1/models` pre-flight), `ollama_chat` (explicit `options.num_ctx`
   on every call), `resolve_task_model` (the per-task overrides),
   `estimate_tokens` (the over-budget guard), `CloudLLMError` family
   (clear errors, no raw tracebacks).
2. JSON mode: `response_format: {"type": "json_object"}` is sent when a
   call wants JSON; a server that answers **400** to the parameter gets
   exactly ONE retry without it, and the rejection is memoized per base
   URL (`_JSON_MODE_REJECTED`) — one doomed attempt per process, never
   one per call.
3. Wiring (surgical hooks in `gui/app.py`): `_call_cloud_llm` is now a
   thin delegate to `openai_chat` (same static signature + new
   `json_mode`/`timeout_s`/`num_ctx`/`on_warn` kwargs); both routers
   (`_llm_analyze._call_llm` and `_run_website_phase._llm_call`) honor
   `models.classify` / `models.analyze` and pass `num_ctx` + the warning
   sink; the cloud branch of `run()` logs the relabeled provider line
   and runs the `/v1/models` pre-flight (warn-never-block) covering the
   default model AND the per-task overrides.
4. Settings: the radio and the group box import the single
   `CLOUD_PROVIDER_LABEL` constant
   ("OpenAI-compatible endpoint (llama.cpp, vLLM, LM Studio, cloud)");
   a new shared **Context window (tokens)** field persists
   `llm_num_ctx`; Test Connection starts with the `/v1/models`
   pre-flight before the classic "Say hello" ping; `--cli --init`
   choice 2 and every status line use the new wording (config VALUE
   stays `cloud` — old configs load unchanged).
5. The deferred Phase-3 few-shot item: the w01 category prompt gained a
   `PAST_CORRECTIONS` slot; `WebsitePipeline._correction_examples()`
   builds it from the corrections log (this URL's own history first,
   then the owner's three most recent moves in the Websites vault,
   deduped; `"(none)"` when empty). `_llm_json` gained a `task` tag
   ('classify' for w01/w02, 'analyze' for w03) — the injected
   `llm_call` contract is now `llm_call(messages, task=None) -> str`.
6. Tools: `run_golden_websites.py --live --backend openai|ollama`
   (model pre-flight per backend, `--num-ctx`, reports named
   `…live-openai.md` / `…live-ollama.md`); `backfill_websites.py` routes
   both providers through the shared helpers + task overrides.

**New config (optional, safe defaults):** `llm_num_ctx` (8192; 0 = let
the server decide) and `models` (`{"classify": "", "analyze": ""}` —
empty = the single configured model).

---

## 2. SPEC → what was actually built

| SPEC §6 Phase 4 | Status |
|---|---|
| Relabel "Cloud API" → "OpenAI-compatible endpoint (llama.cpp, vLLM, LM Studio, cloud)" in GUI, CLI, README | done — one constant (`llm_client.CLOUD_PROVIDER_LABEL`), imported by the radio + group box; CLI wizard + status lines; README section; a test locks the wording |
| Route the cloud path through the same timeout wrapper as Ollama | done — `openai_chat` wraps the POST in `call_with_timeout` (config `llm_timeout_s`, was a hardcoded 120s socket timeout); the socket timeout sits 5s ABOVE the wall clock so the wrapper is the deterministic authority (callers get the same `TimeoutError` as on the Ollama path) |
| Send JSON-mode (`response_format`) where supported, fall back cleanly when rejected | done — sent on every JSON-expecting call; 400 → one retry without it → memoized per endpoint; covered by 5 dedicated tests incl. "keeps failing → the original error surfaces" |
| Set the context window explicitly (Ollama `num_ctx`; the equivalent server setting elsewhere), never truncate silently | done — `options.num_ctx` on EVERY Ollama call (default 8192; Ollama's own default silently chops long prompts from the front); OpenAI-compatible servers fix the window at launch (llama.cpp `-c` / vLLM `--max-model-len` — documented in the tooltip + README), so the client's job is the over-budget WARNING: `estimate_tokens` vs `llm_num_ctx`, logged BEFORE the call on both providers |
| A `/v1/models` pre-flight check | done — at batch start (default model + both overrides; warn-never-block) and as step 1 of Test Connection; hidden `/models` routes are reported, not punished |
| Optional per-task model override (`models.classify`, `models.analyze`) | done — `resolve_task_model` + both worker routers + the backfill; applies to both providers |
| Tests against a fake local HTTP server: success, timeout, malformed JSON, server that rejects `response_format` | done — `tests/test_phase4.py`: 45 tests; the fake server is a stdlib `http.server`; plus the REAL ollama client library against a fake Ollama server (proves `options.num_ctx` reaches the wire) |
| Acceptance: CI green; golden set runnable on Ollama and on a llama.cpp server; comparison report | done — gate 28 compiles + 320/320 + offline golden 30/30 (0 invalid); both backends run the golden set (see `docs/reports/golden-backends-report.md`); mirror gate dispatched on the branch |

## 3. The owner's 5-minute self-check

1. `python main.py` → Settings → 🧠 LLM — read the new radio label,
   the **Context window** field (8192 default), and the group title.
2. Pick the OpenAI-compatible option and point it at your llama.cpp
   server (`http://localhost:8080/v1`, no key needed) → More → Test
   Connection: the log shows the `/models` check first, then the chat
   reply.
3. (Optional) add `"models": {"classify": "llama3:70b"}` to
   `config.json` → the next run logs the pre-flight line for it and
   routes w01/w02 there.
4. `python gitcurator/tools/run_golden_websites.py --live --backend
   ollama --model llama3` (and `--backend openai` against llama.cpp) —
   compare `app/reports/golden/websites-report-live-*.md`.
5. Move a website note by hand, run once (the correction is logged),
   then process a similar site — the log/model answer now sees your
   correction as an example.

## 4. What I verified end to end (sandbox)

- The full gate (28 compiles + 320/320 + offline golden 30/30, 0
  invalid).
- The live golden set on the OpenAI-compatible protocol through a real
  HTTP server (the sandbox llm-shim over the z-ai SDK — the same
  protocol llama.cpp serves), including the JSON-mode `response_format`
  field being sent on every classification call.
- The live golden set through the REAL `ollama` client library against
  an Ollama-API bridge (disclosed in the backends report) — proving the
  full `--backend ollama` path: SDK request shape, `options.num_ctx` on
  the wire, `format: json`, response parsing.
- A genuine Ollama/llama.cpp **server with a real model could not run
  in this sandbox** (no server binaries, no GPU) — the exact commands
  for the owner's machine are in the backends report §"How to run it
  yourself".

## 5. Decisions made (with the rejected alternative)

1. **The timeout wrapper owns the deadline; the socket timeout sits 5s
   above it.** Rejected: equal timeouts (a race — the socket error
   would surface as `URLError` instead of the `TimeoutError` callers
   already handle for Ollama).
2. **The response_format fallback retries once and memoizes per base
   URL.** Rejected: parsing the 400 body for "response_format" (bodies
   vary wildly across servers); retry-once-without is universal and
   costs one request, once per process.
3. **`_call_cloud_llm` stays a staticmethod with the old 4-arg
   signature plus new kwargs.** Rejected: moving it into the worker
   routers (the Settings Test Connection calls it without a worker).
4. **SSL verification stays OFF** (v26 behavior): self-hosted
   llama.cpp / LM Studio endpoints often run self-signed certs. Flagged
   for a future hardening pass (a `verify_tls` config knob now exists
   in `openai_chat` as the seam).
5. **`llm_num_ctx` defaults to 8192, not "unset".** The SPEC says set
   the window EXPLICITLY — leaving it unset recreates the silent
   truncation on Ollama. `0` is the explicit "server decides" opt-out.
6. **The corrections few-shot hook is capped and deduped** (own history
   ≤3, recent others ≤3 distinct pairs). Rejected: the whole log (grows
   unbounded, wastes the classify context budget).
7. **The `llm_call` contract grew `task=None`** and every fake in the
   repo was updated in the same commit. Rejected: prompt-sniffing the
   task from message text (fragile, implicit).

## 6. Risks and unknowns

- The owner's Cloudflare token still lacks Workers AI permission (see
  STATUS "Inputs still needed") — until fixed, the OpenAI-compatible
  endpoint in production is llama.cpp/LM Studio local or another
  provider, not Cloudflare.
- `response_format` acceptance varies across servers; the fallback
  covers rejection, and the memo means a rejecting server pays for
  exactly one extra request per process.
- The token estimate (`~4 chars/token`) is deliberately rough — it
   only fires a WARNING, never blocks a call.

## 7. Numbers

- 45 new tests (`tests/test_phase4.py`); full suite 320 (was 275).
- `llm_client.py` 169 → ~560 lines (the shared client section).
- Surgical hooks: `gui/app.py` +~120 lines net (relabel + pre-flight +
  ctx field + routers), `cli.py` ~15, `website_pipeline.py` +~70
  (task tags + corrections examples), tools ~90.

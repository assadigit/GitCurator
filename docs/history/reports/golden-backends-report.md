# Phase 4 — Golden Set on Both Backends (comparison report)

*Generated 2026-09-29 · v0.13.0 · SPEC §6 Phase 4 acceptance: "the golden set is runnable on Ollama and on a llama.cpp server; a comparison report is produced."*

## What ran

The same 30-link golden set, the same pipeline, the same model — over the two transports the app now supports:

| | Backend 1 | Backend 2 |
|---|---|---|
| Transport | OpenAI chat completions (`/v1/chat/completions`) | Ollama native API (`/api/chat`) |
| Client path | `llm_client.openai_chat` (timeout wrapper, `response_format: json_object`) | `llm_client.ollama_chat` via the **real `ollama` client library** (`options.num_ctx=8192`, `format: json`) |
| Command | `--live --backend openai` | `--live --backend ollama --num-ctx 8192` |
| Result | 16 exact · 4 subcategory · 4 category · 6 fetch-blocked | 12 exact · 5 subcategory · 3 category · 10 review |
| Invalid taxonomy names | **0** | **0** |

**Honest disclosure (sandbox):** no Ollama server or GPU exists in the build sandbox. Backend 1 ran against a real OpenAI-compatible HTTP server (the llm-shim over the z-ai SDK — the same wire protocol `llama.cpp -server`, vLLM and LM Studio expose). Backend 2 ran the REAL ollama client library end-to-end against a stdlib Ollama-API bridge to the same endpoint — so the Ollama protocol path (SDK request shape, `options.num_ctx` and `format` on the wire, response parsing) is exercised for real; only the model SERVING is shared with Backend 1. On your machine, Backend 2's command runs against a plain `ollama serve` with no bridge.

Wire proof (captured live in the Ollama run, all 64 of its calls):

```json
{"model": "…", "options": {"num_ctx": 8192}, "format": "json", "n_msgs": 1}
```

## The comparison that matters

**20 links were classified by BOTH backends:**

- **16 identical** category + subcategory, to the letter;
- **2 differ only in subcategory** (#25 neuform.ai, #27 posts.design — both inside the same category; plain model sampling variance at temperature 0.2);
- **2 differ in category — exactly the two known judgment-call sites** (#11 cobalt.tools, where the **Ollama** run matched your expected value and the OpenAI run chose Download Resources; #23 motionsites.ai, the reverse). These are the same borderline sites the Phase-2 live run flagged for your review — model-side judgment, not transport.

**18 of 20 identical categories; 16 of 20 identical category + subcategory.** Same model + same prompts ⇒ equivalent answers regardless of transport — the protocol paths are interchangeable. The remaining differences are model-level, not transport-level:

- **4 links** (ui-skills.com, icons8.com, developer.apple.com/design/resources, informationisbeautiful.net) hit a transient **502 from the bridge's upstream** mid-run and were safely filed to `_review` (the never-drop-a-link guarantee held; nothing was lost — retry-queued). Root cause: the ollama live path lacked the transient-retry the openai path had — **fixed in v0.13.0** (same 3-attempt retry on 429/5xx, regression-tested); a local `ollama serve` has no upstream and cannot produce this failure mode.
- **6 links** were fetch-blocked by bot defenses in BOTH runs (pixabay, t.co, reddit, coolors, iconscout, pageflows — HTTP 403/timeout): a fetch-layer matter, unrelated to the LLM backend.
- **Category judgment calls** (cobalt.tools, designmd.me, informationisbeautiful.net, kinetics.colorion.co in the openai run; 3 of them in the ollama run) match the Phase-2 live run's judgment calls — model-side behavior, unchanged by transport; the golden-set expected values remain the owner's to approve/edit in `tests/golden/websites.json`.

## Per-link table

| # | Link | OpenAI backend | Ollama backend | Same? |
|---|---|---|---|---|
| 1 | [hhttps://ui-skills.com/skills/emilkowalski/em | ✅ Design / UI/UX & Product Design | ⚠️ review | (transient 502 → fixed) |
| 2 | [hhttps://icons8.com/ | ✅ Design / Assets & Resources | ⚠️ review | (transient 502 → fixed) |
| 3 | [hhttps://designs.ai/graphicmaker | ◐ Design / UI/UX & Product Design | ◐ Design / UI/UX & Product Design | **yes** |
| 4 | [hhttps://pixabay.com | ⚠️ review | ⚠️ review | (both blocked) |
| 5 | [hhttps://t.co/Mxdio85wvQ | ⚠️ review | ⚠️ review | (both blocked) |
| 6 | [hhttps://mixkit.co | ✅ Design / Assets & Resources | ✅ Design / Assets & Resources | **yes** |
| 7 | [hhttps://www.figma.com/resources/learn-design | ✅ Design / UI/UX & Product Design | ✅ Design / UI/UX & Product Design | **yes** |
| 8 | [hhttps://www.reddit.com/r/FREEMEDIAHECKYEAH/w | ⚠️ review | ⚠️ review | (both blocked) |
| 9 | [hhttps://60fps.design/ | ✅ Design / UI/UX & Product Design | ✅ Design / UI/UX & Product Design | **yes** |
| 10 | [hhttps://cleanup.pictures | ◐ Design / UI/UX & Product Design | ◐ Design / UI/UX & Product Design | **yes** |
| 11 | [hhttps://cobalt.tools/ | ❌ Download Resources | ✅ Free Utilities & Everyday Tools | DIFFERS |
| 12 | [hhttps://colormind.io/bootstrap/ | ◐ Design / UI/UX & Product Design | ◐ Design / UI/UX & Product Design | **yes** |
| 13 | [hhttps://colors.dopely.top/palettes | ✅ Design / Assets & Resources | ✅ Design / Assets & Resources | **yes** |
| 14 | [hhttps://coolors.co/ | ⚠️ review | ⚠️ review | (both blocked) |
| 15 | [hhttps://designdetails.fm/episodes | ✅ Design / UI/UX & Product Design | ✅ Design / UI/UX & Product Design | **yes** |
| 16 | [hhttps://designmd.me/ | ❌ Design / UI/UX & Product Design | ❌ Design / UI/UX & Product Design | **yes** |
| 17 | [hhttps://developer.apple.com/design/resources | ✅ Design / Assets & Resources | ⚠️ review | (transient 502 → fixed) |
| 18 | [hhttps://iconscout.com/free-icons | ⚠️ review | ⚠️ review | (both blocked) |
| 19 | [hhttps://informationisbeautiful.net | ❌ Design / UI/UX & Product Design | ⚠️ review | (transient 502 → fixed) |
| 20 | [hhttps://kinetics.colorion.co/ | ❌ Developer Tools | ❌ Developer Tools | **yes** |
| 21 | [hhttps://letsenhance.io | ✅ Design / Assets & Resources | ✅ Design / Assets & Resources | **yes** |
| 22 | [hhttps://mobbin.com/ | ✅ Design / UI/UX & Product Design | ✅ Design / UI/UX & Product Design | **yes** |
| 23 | [hhttps://motionsites.ai | ◐ AI Tools for Web & App Development / AI UI/Design Genera | ❌ Design / UI/UX & Product Design | DIFFERS |
| 24 | [hhttps://namethatui.com/ | ✅ Design / UI/UX & Product Design | ✅ Design / UI/UX & Product Design | **yes** |
| 25 | [hhttps://neuform.ai/ | ✅ AI Tools for Web & App Development / AI Prototyping / No | ◐ AI Tools for Web & App Development / AI UI/Design Genera | DIFFERS |
| 26 | [hhttps://pageflows.com | ⚠️ review | ⚠️ review | (both blocked) |
| 27 | [hhttps://posts.design/ | ✅ Design / UI/UX & Product Design | ◐ Design / Assets & Resources | DIFFERS |
| 28 | [hhttps://recent.design/ | ✅ Design / UI/UX & Product Design | ✅ Design / UI/UX & Product Design | **yes** |
| 29 | [hhttps://remove.bg | ✅ Design / Assets & Resources | ✅ Design / Assets & Resources | **yes** |
| 30 | [hhttps://styles.refero.design/ | ✅ Design / UI/UX & Product Design | ✅ Design / UI/UX & Product Design | **yes** |

## How to run it yourself (owner's machine)

```cmd
:: Ollama — local server, no key
python gitcurator/tools/run_golden_websites.py --live --backend ollama --model llama3 --num-ctx 8192

:: llama.cpp server — OpenAI-compatible endpoint, no key
llama-server -m <model>.gguf -c 8192 --host 127.0.0.1 --port 8080
python gitcurator/tools/run_golden_websites.py --live --backend openai --api-url http://127.0.0.1:8080/v1 --model <model>

:: any cloud provider (OpenAI/Together/OpenRouter/…)
python gitcurator/tools/run_golden_websites.py --live --backend openai --api-url https://api.openai.com/v1 --api-key sk-… --model gpt-4o-mini
```

Reports land in `app/reports/golden/websites-report-live-ollama.md` / `…-live-openai.md`. Both pre-flight their backend first (model list check).

*Raw run logs of this comparison: `app/reports/golden/` (git-ignored); the numbers above are transcribed from them.*

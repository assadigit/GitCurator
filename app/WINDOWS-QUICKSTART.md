# GitCurator — first run on Windows (v0.14.1)

Three steps and you're in.

## 1. Install (once)

Double-click **`1-INSTALL.bat`**.

- It creates a private Python environment (`.venv` folder next to the
  app) and installs everything the app needs. Nothing is installed
  globally on your computer.
- Takes 1–2 minutes, needs internet.
- **If it says Python was not found:** install Python from
  <https://www.python.org/downloads/>, and **tick "Add python.exe to
  PATH"** on the first installer screen — then double-click
  `1-INSTALL.bat` again.
- Safe to re-run any time.

## 2. Start the app

Double-click **`GitCurator.bat`** → the desktop app opens.

A black console window opens behind it — that's normal (it shows the
app's log lines). If anything ever breaks, that window has the exact
error: screenshot it.

First launch runs with empty settings. Two ways to configure
(pick either):

- **In the app:** Settings — your GitHub token, the LLM (local Ollama
  or any OpenAI-compatible endpoint), and 📁 Vault (your three vault
  paths, with live status per picker).
- **Or the wizard:** double-click `GitCurator-CLI-Setup.bat` — asks
  for the same things in the terminal and saves the same config.

## 3. Safe first run (writes NOTHING)

Double-click **`GitCurator-DRY-RUN.bat`**.

It performs the same fully-automatic run as the real thing — checks,
queue, processing — but every write is only **reported**. No notes are
created, nothing touches your vaults, no backups are pushed. Read what
it says it *would* do. When you're happy: the real run is
`GitCurator-CLI.bat` (or the Start button in the app).

## Is everything up and ready? — Test Connection

In the app, click **Test Connection** (next to SYNC). It checks and
shows IN THE LOG, one line each: **vaults** (found + writable), **LLM**
(Ollama / llama.cpp / API — whichever is active), **GitHub** (token +
backup repos) and **Telegram** (bot + account login, with a LIVE
connection test). Ends with `🏁 ALL SYSTEMS READY` or a list of what
needs attention. Same thing in a terminal — double-click

**`GitCurator-TEST-CONNECTION.bat`**

(or `python main.py --cli --test-connection`)

## Your vault paths (from the build notes)

| Vault | Path | Where to set |
|---|---|---|
| GitHub Projects | `G:\Docs\Github Projects Obsidian Vault` | Settings → 📁 Vault |
| Websites | `G:\Docs\Documents\Obsidian Website Directory` | Settings → 📁 Vault |
| Manual Notes | `C:\Users\Ali Assadi\Documents\Obsidian Vault` | Settings → 📁 Vault |

The Websites pipeline is OFF by default — flip the switch in the app
when you want it. The Manual Notes vault is yours; the app only ever
writes its read-only `Library\` mirror inside it, and only when you run
the mirror tool yourself.

## What each file is

| File | What it is |
|---|---|
| `1-INSTALL.bat` | one-time install (safe to re-run) |
| `GitCurator.bat` | **the desktop app** |
| `GitCurator-DRY-RUN.bat` | full automatic run, writes nothing |
| `GitCurator-CLI.bat` | full automatic run (real) |
| `GitCurator-TEST-CONNECTION.bat` | is everything up and ready? (vaults · LLM · GitHub · Telegram live) |
| `GitCurator-CLI-Setup.bat` | first-run credential wizard |
| `Start-GitCurator-CLI.bat` | same as GitCurator-CLI.bat (kept for muscle memory) |
| `config.json` | created when you save settings — your credentials, keep it private |
| `config.example.json` | the settings template (safe to look at) |
| `cache.db` | the app's memory (created on first run) |
| `reports\` | run reports, dry-run plans, vault scans land here |
| `README.md` | the full manual |

## Good to know

- **Blocked sites (x.com / YouTube)?** The Websites pipeline fetches
  through the SAME proxy as Telegram: Settings → 🌐 Proxy → "Use this
  proxy for web fetches too" (on by default). With v2rayN running,
  the log shows `🌐 Web fetches via SOCKS5 127.0.0.1:10808` at the
  start of a batch and those links come through; links that failed
  before are re-armed and retried automatically (`🔁 Web proxy active
  — re-armed N queued retry(ies)`). Ollama / llama.cpp traffic on
  127.0.0.1 is NEVER proxied.
- **X/Twitter links are never fetched** (they are already recorded in
  the `_inbox/x_twitter_links.md` table): Settings → 📁 Vault →
  "Blocked domains" (default x.com, twitter.com, t.co). The bot-queue
  view shows them in their own 🚫 bucket; queued ones are purged
  automatically.
- **404 GitHub repos stop counting as pending**: they get a placeholder
  note in `_missing/` (a batch also backfills past 404s
  automatically). To re-check a repo: delete its note + reset it in
  More ▸ View 404 Quarantine.
- **The `_inbox` platform tables live in the WEBSITES vault now** (the
  GitHub vault keeps only github.com links).
- **LLM:** the default is local Ollama (`http://localhost:11434`).
  Don't have it? Install from <https://ollama.com>, then run
  `ollama pull llama3.1` — or use a local **llama.cpp** server
  (llama-server): the app detects it AUTOMATICALLY at launch (whatever
  port it runs on) and switches to it when Ollama isn't running.
  **Two options in Settings → 🧠 LLM** (v0.23.0): 🖥️ **Locally hosted
  LLM model** (Ollama / llama.cpp engines) or ☁️ **Cloud API model** —
  any OpenAI-compatible endpoint **or Anthropic Claude** (an
  `api.anthropic.com` URL switches the wire format automatically).
  **Switching between the local engines?** The **🧠 Detect & Set Ollama**
  and **🦙 Detect & Set llama.cpp** buttons (Settings → 🧠 LLM, inside
  the Locally hosted group) do it in one click: probe the engine, pick
  the model when several are installed, set + save. Terminal twin:
  `python main.py --cli --detect-llm ollama` (or `llamacpp`).
- Everything the app writes stays inside this folder (`config.json`,
  `cache.db`, `reports\`) or inside the vaults you point it at.
- **Updating later:** unzip the new version and copy your old
  `config.json` (and `cache.db` if you want the app to remember
  processed links) into the new folder.
- The optional Cloudflare bot + web dashboard are **not** in this zip;
  they live in the repository.

Version 0.20.0 — the phased build: vault settings & ownership stamps,
the websites pipeline, moves-as-corrections + the backfill, LLM
backends, the Manual Notes Library mirror, Test Connection, the
Detect & Set LLM quick-switch, web fetches through your proxy, and
the intake truth (blocked domains · missing-repo notes · vault
separation). Full manual: `README.md` in this folder.

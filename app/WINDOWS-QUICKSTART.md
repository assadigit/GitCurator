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
| `GitCurator-CLI-Setup.bat` | first-run credential wizard |
| `Start-GitCurator-CLI.bat` | same as GitCurator-CLI.bat (kept for muscle memory) |
| `config.json` | created when you save settings — your credentials, keep it private |
| `config.example.json` | the settings template (safe to look at) |
| `cache.db` | the app's memory (created on first run) |
| `reports\` | run reports, dry-run plans, vault scans land here |
| `README.md` | the full manual |

## Good to know

- **LLM:** the default is local Ollama (`http://localhost:11434`).
  Don't have it? Install from <https://ollama.com>, then run
  `ollama pull llama3.1` — or use a local **llama.cpp** server
  (llama-server): the app detects it AUTOMATICALLY at launch (whatever
  port it runs on) and switches to it when Ollama isn't running —
  or pick any OpenAI-compatible endpoint in Settings → 🧠 LLM.
- Everything the app writes stays inside this folder (`config.json`,
  `cache.db`, `reports\`) or inside the vaults you point it at.
- **Updating later:** unzip the new version and copy your old
  `config.json` (and `cache.db` if you want the app to remember
  processed links) into the new folder.
- The optional Cloudflare bot + web dashboard are **not** in this zip;
  they live in the repository.

Version 0.14.1 — the phased build: vault settings & ownership stamps,
the websites pipeline, moves-as-corrections + the backfill, LLM
backends, and the Manual Notes Library mirror. Full manual:
`README.md` in this folder.

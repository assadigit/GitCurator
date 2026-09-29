#!/usr/bin/env python3
"""
gitcurator.constants — shared configuration defaults & design tokens.

Carved out of the flat main.py in v32 (modular package layout) so every
module (GUI, pipeline, headless CLI, integrations, CI tests) reads ONE
source of truth for:

  * APP_DIR              — the application root (the folder that contains
                           the gitcurator package: config.json, assets/,
                           prompts/, tests/ all live here)
  * CONFIG_FILE          — config.json, now anchored to APP_DIR (was
                           cwd-relative; same file when launched from
                           app/, correct file when launched from anywhere)
  * COLORS               — design tokens used by the QSS builders
  * CONFIG_EXAMPLE       — the default config dict (structure doc)
  * CATEGORY_FOLDERS     — LLM category -> vault subfolder mapping
  * DEFAULT_SYSTEM_PROMPT— the curator's analysis prompt

Pure stdlib. No PyQt imports — importable from the headless CLI, the
integration subprocess scripts and the CI test gate.
"""

import os

# The application root = the folder that CONTAINS this package
# (.../app/gitcurator/constants.py -> .../app).
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_FILE = os.path.join(APP_DIR, "config.json")

# Design System — PASTEL (v32): mint / violet / rose / butter on cream & plum
# ============================================================================
# The whole UI speaks pastel while every text pair keeps WCAG AA (>=4.5:1).
# The trick: pastel FILLS always carry a deep companion TEXT color; pure
# accents (tab underline, links, status text) are deep enough on their own.
#
# PRIMARY (filled):      Pastel mint #B9E3C9 + deep-forest text #17402B
#                         (8.3:1). Exactly ONE filled primary button per tab.
# SECONDARY (outlined):  Violet family — #5F54B4 text on white 6.2:1 (AA);
#                         pastel lavender #C4BCF5 is the dark-mode accent
#                         (8.2:1 on the plum panels).
# DANGER (filled):       Pastel rose #F6C6CD + deep-rose text #5E1120 (8.8:1).
#                         Destructive actions ONLY (Stop, Remove, Undo).
# NEUTRAL: warm mauve — muted text, borders, tertiary utilities.
# SEMANTIC: *_deep keys are TEXT colors for light panels; the bare keys are
#                         fills or dark-mode accents as noted below.
#
# Layout tokens (v31.1, unchanged): spacing scale 4/8/16/24/32/48 — the 7px
# focus padding is the 8px token minus the 1px wider focus border. Type
# scale: 16 titles / 13 labels+buttons / 12 body / 12 mono. Focus outline:
# 2px solid + 2px offset on every interactive element.
COLORS = {
    # Primary action — PASTEL MINT fill with deep-forest text
    'cta':            '#B9E3C9',  # pastel mint fill
    'cta_hover':      '#A8DABA',  # deeper mint (hover/pressed)
    'cta_text':       '#17402B',  # deep-forest text on the fill (8.3:1)

    # Secondary/read action — PASTEL VIOLET family (OUTLINED buttons)
    'primary':        '#5F54B4',  # accent on light surfaces (6.2:1 on white)
    'primary_hover':  '#514699',  # deep violet (hover fills carry white 8.6:1)
    'primary_dark':   '#C4BCF5',  # pastel lavender accent on dark (8.2:1)

    # Neutral (warm mauve) — muted text, borders
    'neutral':        '#6C6480',  # muted mauve (5.6:1 on white)
    'neutral_hover':  '#57506B',  # deeper mauve

    # Semantic — pastel fills pair with deep text; *_deep = text on light
    'error':          '#F6C6CD',  # pastel rose fill
    'error_hover':    '#F0B2BC',  # deeper rose (hover/pressed)
    'error_text':     '#5E1120',  # deep rose text on the fill (8.8:1)
    'error_deep':     '#AE2237',  # error TEXT on light panels (6.8:1)
    'warning':        '#8A5B0B',  # warning text on light (5.9:1)
    'warning_pastel': '#F7E7BE',  # butter fill (pairs with #5C430B, 7.6:1)
    'success':        '#1E6B4B',  # success text on light (6.4:1)

    # v0.07 main-screen tokens (design review) — ONE accent (the lavender
    # anchor) for interactive chrome; green/red/butter reserved for states.
    'hero_fill':       '#C4BCF5',  # SYNC/PROCESS fill (both themes)
    'hero_fill_hover': '#D3CDF9',
    'hero_text':       '#241D3F',  # deep plum on the lavender fill (9.0:1)
    'danger_fill':      '#D63A24',  # STOP kill-switch fill (white 4.7:1)
    'danger_fill_hover':'#C43320',  # darker hover (white 5.5:1)
    'danger_fill_press':'#B32D1D',
    'log_well_dark':   '#17131F',  # recessed log well on plum (figure-ground)
    'log_well_light':  '#FFFFFF',
    'hint_dark':       '#A6A2AC',  # placeholder text, AA on plum sheets
    'hint_light':      '#7A7288',  # placeholder text, AA on white

    # Backgrounds — cream day / plum night
    'bg_light':       '#FBF8F2',  # warm cream
    'bg_dark':        '#221E2E',  # soft plum-charcoal
}
CONFIG_EXAMPLE = {
    "telegram_api_id": 0,
    "telegram_api_hash": "",
    "telegram_phone": "",
    "proxy": {
        "enabled": False,
        "type": "socks5",
        "host": "127.0.0.1",
        "port": 10808,
        # v0.19.0 — the Websites pipeline rides the same proxy (blocked-web
        # fix: x.com / t.co / youtu.be are connection-refused direct).
        "use_for_web": True
    },
    # v0.20.0 — domains the Websites pipeline NEVER fetches (they are
    # already recorded as rows in the _inbox platform tables). Settings →
    # 📁 Vault → "Blocked domains"; an empty list = allow all.
    "web_blocked_domains": ["x.com", "twitter.com", "t.co"],
    # v0.21.0 — hosts that belong to THIS deployment (the Telegram bot's
    # own worker): its auth links (…/auth/?token=…) are never fetched and
    # never noted; the _inbox row (secret query values scrubbed) is the
    # record. Settings → 📁 Vault → "Self domains"; empty list = none.
    "web_self_domains": ["github-to-obsidian-bot.aliassadi-plus.workers.dev"],
    "ollama": {
        "base_url": "http://localhost:11434",
        "model": "qwythos-9b"
    },
    # v26 — Fix 4: Cloud LLM API support (OpenAI-compatible).
    # ``llm_provider`` selects the backend used by ProcessingWorker._llm_analyze.
    # 'ollama' (default) keeps the existing local-Ollama flow.
    # 'cloud' switches to an OpenAI-compatible HTTP API (any provider that
    # exposes /v1/chat/completions — OpenAI, Together, OpenRouter, etc.).
    "llm_provider": "ollama",
    "cloud_api_url": "https://api.openai.com/v1",
    "cloud_api_key": "",
    "cloud_model": "gpt-4o-mini",
    # v0.15.0 — llama.cpp engine detection: llama-server as its OWN
    # provider value ('llamacpp'), detected like Ollama instead of
    # hand-configured like the cloud endpoint. llamacpp_model empty =
    # AUTO-DETECTED from the running server (/v1/models, /props alias).
    "llamacpp_api_url": "http://127.0.0.1:8080/v1",
    "llamacpp_api_key": "",
    "llamacpp_model": "",
    # v0.16.0 — Phase 6 (Linking). The embedding model for the linking
    # layer (recall field + one-line + tags, never full text). Empty =
    # the provider default: Ollama 'nomic-embed-text'; llama.cpp uses
    # the served model (llama-server needs --embeddings); a cloud
    # endpoint falls back to cloud_model when this is empty.
    "embedding_model": "",
    "github_token": "",
    "vault_path": "",
    "vaults_history": [],
    "log_level": "INFO",
    "timeout_per_repo": 60,
    "max_retries": 3,
    "delay_between_api_calls": 0.5,
    # v28 — Cloudflare bot sync + Google Drive backup
    "cloudflare_enabled": False,
    "cloudflare_worker_url": "",
    "cloudflare_install_id": "",
    "cloudflare_shared_secret": "",
    "cloudflare_poll_interval": 300,
    "gdrive_enabled": False,
    "gdrive_client_id": "",
    "gdrive_client_secret": "",
    "gdrive_access_token": "",
    "gdrive_refresh_token": "",
    "gdrive_token_expiry": 0,
    "gdrive_folder_id": "",
    "gdrive_max_backups": 10,
    "gdrive_redirect_port": 8765,
    # v31 — VaultSeal: automatic post-run vault backup to a PRIVATE GitHub
    # repository. Obsidian's free tier has no sync — after every curation
    # run (1 repo or 100) the whole vault is committed and pushed. Restore
    # is plain git clone; machine state (workspace.json, .trash) is
    # excluded automatically. See vaultseal.py.
    "vaultseal": {
        "enabled": True,     # seal after every run (unchanged vault = no-op)
        "repo_name": "",      # empty = derived from the vault folder name
        "auto_push": True    # push to GitHub (needs github_token)
    },
    # v32 — GoodRepos: post-run publisher of the PUBLIC curated directory.
    # Every curated repo becomes an entry in an emoji-rich README (organized
    # like AI → Skills → …) with the full notes mirrored into category
    # folders, in a PUBLIC repository — everyone benefits from the
    # curation. See gitcurator/integrations/goodrepos.py.
    "goodrepos": {
        "enabled": True,       # publish after every run (unchanged = no-op)
        "repo_name": "good-repos",
        "auto_push": True      # push to GitHub (needs github_token)
    }
}

# Category folder mapping
CATEGORY_FOLDERS = {
    "Agents": "AI-Domain/Agents",
    "Agents/Frameworks": "AI-Domain/Agents/Frameworks",
    "Agents/Implementations": "AI-Domain/Agents/Implementations",
    "Agents/Skills": "AI-Domain/Agents/Skills",
    "MCP": "AI-Domain/MCP",
    "Skills": "AI-Domain/Skills",
    "LLM-Tools": "AI-Domain/LLM-Tools",
    "Scraping": "Tools/Scraping",
    "Automation": "Tools/Automation",
    "Dev-Tools": "Tools/Dev-Tools",
    "Networking": "Tools/Networking",
    "Media": "Tools/Media",
    "Guides": "Documentation/Guides",
    "References": "Documentation/References",
    "Research": "Documentation/Research",
    "Web-Frameworks": "Frameworks/Web-Frameworks",
    "Backend": "Frameworks/Backend",
    "Frontend": "Frameworks/Frontend",
    "Infrastructure": "Infrastructure",
    "Infrastructure/Deployment": "Infrastructure/Deployment",
    "Infrastructure/Cloud": "Infrastructure/Cloud",
    "Infrastructure/Containerization": "Infrastructure/Containerization",
    "Infrastructure/Documentation": "Infrastructure/Documentation",
    "Uncategorized": "Uncategorized"
}
CATEGORY_KEYS = list(CATEGORY_FOLDERS.keys())

# Default system prompt
DEFAULT_SYSTEM_PROMPT = """You are an expert software engineer and technical writer. Your task is to analyze GitHub repositories and provide detailed, structured insights.

For each project, you must output a JSON object with the following keys:
- "short_summary": A single-sentence summary of the project in LESS THAN 140 characters. State the core utility/value directly.
- "summary": A 2-3 paragraph summary explaining what the project does, its main purpose, and its key features. (This is the "What is it?" section.)
- "how_it_works": A 1-2 paragraph explanation of the project's architecture, key technologies, and how it operates.
- "core_value": A 1-2 paragraph explanation of why this project is important, what problem it solves, and its unique value proposition. If user context is provided, explain how it specifically helps that user.
- "features": A list of 3-7 key features or technologies used (e.g., ["Uses Cloudflare Workers", "Integrates with Gmail API", "Self-hosted"]).
- "difference": A 1-2 paragraph comparison with similar projects, explaining what makes this one stand out.
- "category": Choose one category from this list: """ + ', '.join(CATEGORY_KEYS) + """
- "confidence": A number between 0 and 100 indicating how confident you are in the chosen category.
- "tags": A list of up to 5 relevant tags (e.g., ["python", "llm", "agent", "framework", "email"]).

Guidelines:
- Be factual and use the provided data (name, description, topics, owner, stars, forks, README).
- If the project is from a major tech company (Microsoft, Google, Cloudflare, etc.), highlight this.
- Choose the best-fit category even if confidence is moderate. Only use "Uncategorized" if truly unknown.
- The short_summary MUST be under 140 characters and capture the essence of the project.

After generating your analysis, cross-check your output against the README and project data. If you find any factual errors or hallucinations, correct them before returning the JSON. Do not guess or invent information not present in the source data.

Return only valid JSON. Do not include any extra text."""


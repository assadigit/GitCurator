"""gitcurator.cloud — optional cloud integrations.

cloudflare_manager / cloudflare_sync / cloudflare_gui (bot-queue sync)
and gdrive_backup / gdrive_gui (Google Drive vault backups). All
optional: main.py imports them inside try/except and degrades
gracefully when they are missing.
"""

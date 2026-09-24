"""gitcurator.integrations — vault + Telegram integrations.

telethon_fetcher / telegram_fetch_worker (fetch links from Saved
Messages in a separate process), backfill_manager, vaultseal (private
vault git mirror), goodrepos (public curated directory publisher) and
error_reporter. The fetcher scripts bootstrap sys.path themselves so
they keep working when launched directly as subprocesses.
"""

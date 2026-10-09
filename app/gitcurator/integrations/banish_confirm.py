"""BanishGateConfirm — the Telegram round-trip of THE CONFIRMATION GATE.

v0.60.0 — the owner's ask (session, verbatim): "when system is
scanning my vault and fetching from telegram bot, it must show
' X number of notes should be deleted ' do you confirm? … At the
beginning of every run, system scans vault, find what I've marked to
delete, system detects them, show me them their numbers, so I ensure
that system successfully detected them, I confirm deletion, then they
will get deleted from both vaults and never be fetched again."

The pipeline's core keeps the law pure (no network in
website_pipeline); this module is the channel the GUI worker and the
CLI inject: ``WebsitePipeline(..., banish_confirm=make_telegram_confirm(config))``.

The round-trip:

1. ``propose`` — POST /api/banish/propose (HMAC-signed): the Worker
   sends the Telegram message ("🗑️ Deletion review — N notes marked
   for deletion", the list, the two buttons 🗑️ Delete all N /
   ✋ Keep all N) and keeps the ask in its state table.
2. ``poll`` — GET /api/banish/status?id=… every POLL_INTERVAL_S until
   the owner answers or the deadline (config
   ``banish_confirm_timeout_s``, default 300s) expires.
3. ``answer`` — 'confirmed' / 'declined' / 'timeout'. A confirmed ask
   carries ``report``: the pipeline calls it with the count that
   ACTUALLY left, and the Worker edits the Telegram message into the
   closing line ("🗑️ N websites removed — never to be fetched
   again …" — the owner's example wording). A timeout is reported
   here already (the no-answer story edits the message); a decline
   was edited by the Worker at the button press.

Unavailability is honest: ``make_telegram_confirm`` returns None when
the Worker is not paired/enabled — the pipeline then takes the SAFE
default (defer: the marks stay, nothing deleted, the next run asks
again) and says so in the log.
"""

import time
from typing import Callable, Dict, List, Optional

#: How often the ask's status is polled (seconds).
POLL_INTERVAL_S = 3.0
#: How long the run waits for the owner's answer (seconds) — config
#: ``banish_confirm_timeout_s`` overrides.
DEFAULT_TIMEOUT_S = 300.0


def make_telegram_confirm(config: dict,
                          log: Optional[Callable] = None
                          ) -> Optional[Callable]:
    """Build the confirm channel from the app config.

    Returns ``confirm(items, log) -> {'verdict', 'report'}`` (see the
    module docstring) or ``None`` when the Telegram worker is not
    paired/enabled — the pipeline's defer default then applies. Never
    raises; a channel that breaks mid-ask answers 'timeout'-safe
    ('defer' — nothing deleted)."""
    log = log or (lambda *a, **k: None)
    try:
        from gitcurator.cloud.cloudflare_sync import CloudflareSync
        sync = CloudflareSync(config or {})
    except Exception as e:            # pragma: no cover — import safety
        log(f"⚠️ Banish gate channel unavailable: {e}", "warning")
        return None
    if not sync.is_enabled():
        return None

    def confirm(items: List[Dict], ask_log: Optional[Callable] = None
                ) -> Dict:
        ask_log = ask_log or (lambda *a, **k: None)
        cfg = config or {}
        try:
            timeout_s = float(
                cfg.get('banish_confirm_timeout_s', DEFAULT_TIMEOUT_S)
                or DEFAULT_TIMEOUT_S)
        except (TypeError, ValueError):
            timeout_s = DEFAULT_TIMEOUT_S
        payload = [
            {'url': str(it.get('url') or it.get('canonical') or ''),
             'title': str(it.get('title') or ''),
             'marker': str(it.get('marker') or ''),
             'door': str(it.get('door') or '')}
            for it in (items or [])]
        ask_log(
            f"📨 Deletion review sent to Telegram — {len(payload)} "
            f"note(s) marked for deletion, waiting for your answer "
            f"(buttons in the bot chat; up to {int(timeout_s)}s)", "info")
        conf_id, why = sync.propose_banish(
            len(payload), payload, timeout_s=timeout_s)
        if not conf_id:
            ask_log(
                f"⚠️ The deletion review could not reach Telegram "
                f"({why}) — nothing deleted, the marks stay; I'll ask "
                f"again next run", "warning")
            return {'verdict': 'defer', 'report': None}
        verdict = 'timeout'
        deadline = time.monotonic() + max(timeout_s, POLL_INTERVAL_S)
        while time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL_S)
            status = sync.banish_status(conf_id)
            if status is None:
                continue        # a missed poll is not an answer — keep waiting
            status = status.strip().lower()
            if status in ('confirmed', 'declined'):
                verdict = status
                break
            if status in ('superseded', 'timeout', 'done', 'stale'):
                verdict = 'timeout'
                break
        if verdict == 'timeout':
            # the desktop's clock ran out — tell the chat (the message
            # becomes the no-answer story; the marks stay).
            try:
                sync.report_banish_result(conf_id, 'timeout')
            except Exception:
                pass

        def report(deleted: int) -> None:
            """The closing line for a CONFIRMED ask — the count that
            actually left the vault (the owner's example: '10 Websites
            Removed and will never fetch again because you …')."""
            try:
                sync.report_banish_result(
                    conf_id, 'deleted', int(deleted or 0))
            except Exception:
                pass    # best-effort — never fails the run

        return {'verdict': verdict,
                'report': report if verdict == 'confirmed' else None}

    return confirm

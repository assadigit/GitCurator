"""ScanGateConfirm — the Telegram round-trip of THE VAULT SCAN (v0.61.0).

The banish gate's twin (``integrations/banish_confirm.py`` is the
template — the owner's own instruction: "the v0.60.0 banish-gate
round-trip is the template: ask → 🗑️/✋ buttons → act; no response in
300s → safe defer, nothing done"). The scan's ask carries the WHOLE
plan: the deletions (the owner's own auto-delete marks — the v0.60.1
grammar), the LLM's filing proposal (moves + new folders), and the
summary. Nothing is deleted or moved until the owner answers; a
timeout is a safe defer.

The round-trip:

1. ``propose`` — POST /api/scan/propose (HMAC-signed, the 1010 law's
   UA): the Worker sends the Telegram message ("🗂️ Vault scan review —
   N notes marked for deletion, M move(s), K new folder(s)", the
   lists, the two buttons 🗂️ Apply plan / ✋ Keep everything) and
   keeps the ask in its state table (``scan_confirm:<id>``).
2. ``poll`` — GET /api/scan/status?id=… every POLL_INTERVAL_S until
   the owner answers or the deadline (config
   ``scan_confirm_timeout_s``, default 300s) expires.
3. ``answer`` — 'confirmed' / 'declined' / 'timeout'. A confirmed ask
   carries ``report``: the caller applies the plan and reports the
   counts that ACTUALLY landed; the Worker edits the Telegram message
   into the closing line. A timeout is reported here already; a
   decline was edited by the Worker at the button press.

Unavailability is honest: ``make_scan_confirm`` returns None when the
Worker is not paired/enabled — the caller then takes the SAFE default
(defer: the plan is logged and dropped, nothing moves, nothing is
deleted) and says so in the log.
"""

import time
from typing import Callable, Dict, List, Optional

#: How often the ask's status is polled (seconds).
POLL_INTERVAL_S = 3.0
#: How long the scan waits for the owner's answer (seconds) — config
#: ``scan_confirm_timeout_s`` overrides.
DEFAULT_TIMEOUT_S = 300.0


def make_scan_confirm(config: dict,
                      log: Optional[Callable] = None
                      ) -> Optional[Callable]:
    """Build the scan's confirm channel from the app config.

    Returns ``confirm(plan, log) -> {'verdict', 'report'}`` or ``None``
    when the Telegram worker is not paired/enabled (the SAFE default
    then applies at the caller). Never raises; a channel that breaks
    mid-ask answers 'timeout'-safe ('defer' — nothing done)."""
    log = log or (lambda *a, **k: None)
    try:
        from gitcurator.cloud.cloudflare_sync import CloudflareSync
        sync = CloudflareSync(config or {})
    except Exception as e:            # pragma: no cover — import safety
        log(f"⚠️ Scan gate channel unavailable: {e}", "warning")
        return None
    if not sync.is_enabled():
        return None

    def confirm(plan: Dict, ask_log: Optional[Callable] = None
                ) -> Dict:
        ask_log = ask_log or (lambda *a, **k: None)
        cfg = config or {}
        try:
            timeout_s = float(
                cfg.get('scan_confirm_timeout_s', DEFAULT_TIMEOUT_S)
                or DEFAULT_TIMEOUT_S)
        except (TypeError, ValueError):
            timeout_s = DEFAULT_TIMEOUT_S
        plan = plan or {}
        deletions = list(plan.get('deletions') or [])
        moves = list(plan.get('moves') or [])
        new_folders = list(plan.get('new_folders') or [])
        # the ask's payload: the deletions (title/url/marker) + the
        # moves (note → folder) — capped the way the banish ask caps
        # its list (20 shown, the rest counted).
        items = [{'kind': 'delete',
                  'title': str(d.get('title') or d.get('url') or ''),
                  'detail': str(d.get('url') or ''),
                  'marker': str(d.get('marker') or '')}
                 for d in deletions]
        items += [{'kind': 'move',
                   'title': str(m.get('note') or ''),
                   'detail': f"{m.get('from') or '(vault root)'} → "
                             f"{m.get('to') or ''}",
                   'marker': str(m.get('reason') or '')[:120]}
                  for m in moves]
        ask_log(
            f"📨 Vault scan review sent to Telegram — "
            f"{len(deletions)} deletion(s), {len(moves)} move(s), "
            f"{len(new_folders)} new folder(s), waiting for your answer "
            f"(buttons in the bot chat; up to {int(timeout_s)}s)", "info")
        conf_id, why = sync.propose_scan(
            len(deletions), len(moves), len(new_folders), items,
            summary=str(plan.get('summary') or ''),
            timeout_s=timeout_s)
        if not conf_id:
            ask_log(
                f"⚠️ The vault scan review could not reach Telegram "
                f"({why}) — nothing moved, nothing deleted; I'll ask "
                f"again on the next scan", "warning")
            return {'verdict': 'defer', 'report': None}
        verdict = 'timeout'
        deadline = time.monotonic() + max(timeout_s, POLL_INTERVAL_S)
        while time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL_S)
            status = sync.scan_status(conf_id)
            if status is None:
                continue    # a missed poll is not an answer — keep waiting
            status = status.strip().lower()
            if status in ('confirmed', 'declined'):
                verdict = status
                break
            if status in ('superseded', 'timeout', 'done', 'stale'):
                verdict = 'timeout'
                break
        if verdict == 'timeout':
            # the desktop's clock ran out — tell the chat (the message
            # becomes the no-answer story; the vault stays as it was).
            try:
                sync.report_scan_result(conf_id, 'timeout')
            except Exception:
                pass

        def report(applied: int, moved: int = 0,
                   folders: int = 0) -> None:
            """The closing line for a CONFIRMED ask — the counts that
            actually landed."""
            try:
                sync.report_scan_result(
                    conf_id, 'applied', int(applied or 0),
                    moved=int(moved or 0), folders=int(folders or 0))
            except Exception:
                pass    # best-effort — never fails the scan

        return {'verdict': verdict,
                'report': report if verdict == 'confirmed' else None}

    return confirm

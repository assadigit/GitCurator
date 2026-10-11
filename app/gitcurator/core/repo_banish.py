"""repo_banish.py — v0.65.0, THE REPOS BANISHMENT (the GitHub vault's own
trash door).

The owner's ask (session, verbatim): "The same mechanism which we
created for websites to banish theme, delete from vault and never fetch
again, i want the same mechanism to be activated for github projects
vault. so for example i can banish an already processed github repo, so
it never fetches and processed."

The websites mechanism (v0.58.0 the banishment, v0.60.0 the
confirmation gate, v0.62.0 the trash door) is twinned here for the
GitHub-projects vault, law for law:

* THE MARKS — the same grammar, read by the same eyes: a repo note
  whose tags carry 🗑️ (or delete / banish / blacklist / purge /
  auto_delete — frontmatter OR ``#auto-delete`` typed in the body), a
  true ``decommission:`` / ``banish:`` / ``blacklist:`` frontmatter
  key, or the owner's MOVE of the note into the root ``Trash`` folder
  (the move is the verdict). :func:`scan_banished_notes` is
  vault-agnostic — it walks ANY vault reading ``managed_by`` /
  ``source`` / the marks — so the repos twin reuses it verbatim,
  filtered to ``github.com`` repo URLs (a non-repo source in the
  GitHub vault is not this door's business).

* THE GATE — nothing is removed until the owner answers. The count is
  spoken FIRST ("N repo note(s) marked for deletion — asking you to
  confirm"), the ask rides the SAME Telegram round-trip the websites
  gate uses (``banish_confirm.make_telegram_confirm`` — the Worker's
  🗑️ Delete all N / ✋ Keep all N buttons; the message shows the repo
  URLs), and the verdict gates the burial: confirmed -> enforce;
  declined / timeout / no channel -> the marks and the notes stay, the
  next run asks again (config ``banish_confirm: false`` restores the
  auto reflex). The channel is INJECTED — this module never touches
  the network (the hermetic law; the websites pipeline keeps the same
  purity in core/website_pipeline.py).

* THE BURIAL — DB first (the graveyard's law: the never-fetch gates
  must hold even when every file operation below fails): the repo URL
  is ``dismiss``ed in note_state (§4.4 "never re-added" — the
  processing loop's own dismissed skip, checked BEFORE any GitHub API
  call) AND ``settle_repos``ed in the settled ledger (v0.63.3 — the
  queue door never counts it pending again), its ``failed_repos`` row
  is resolved (the retry cry, closed), its ``processed_repos`` row is
  forgotten (the ledger must not outlive the note it pointed at — the
  v0.57 false-success law's twin). THEN the furniture: the note FILE
  leaves the library for ``<vault>/.trash/banished`` (Obsidian's
  hidden trash — recoverable by hand, invisible to VaultIndex and
  note_state), and the vault's own ``_review/DECOMMISSIONED.md`` gains
  the record row (``🗑️ banished — confirmed <date>``). A hand-written
  marked note — or a hand-written note resting in Trash — is KEPT with
  a warning (the sacred law: the app never deletes what it did not
  write).

* THE NEVER-FETCH PROMISE — "it never fetches and processed": a
  banished repo is dismissed (the batch loop's §4.4 skip) + settled
  (the queue door's settled bucket + the batch loop's settled skip) +
  retry-resolved (no "repos need retry" cry) + row-forgotten (no
  cache dedupe resurrection). A re-paste of the same repo link next
  month is skipped before any GitHub API call, every door, every
  intake.

* THE ♻️ DOOR BACK — the record row is revivable: set ♻️ in its Status
  cell (or add a row with ♻️ and the URL — the table's own legend) and
  the next run's :func:`revive_banished_repos` clears the dismissal
  and un-settles the repo, fetched like new again (the exact twin of
  the websites master table's ♻️). A revival is the owner's explicit
  hand — it runs BEFORE the gate, whatever the gate's verdict.

* GITHUB ITSELF — the burial is local: VaultSeal pushes the vault with
  ``git add -A`` and ``.trash/`` is gitignored, so the next seal's
  commit takes the deletion to the backup repo on its own (the same
  promise the websites twin makes).

Dry-run aware throughout (a rehearsal scans and speaks truthfully, the
DB writes land in the shadow cache, the file move is rehearsed, the
note_state dismissal is not recorded — the same law run_start_check
follows). Never raises (bookkeeping never kills a batch). No PyQt, no
network, no LLM — the regex brain's own door.
"""

import os
from datetime import datetime
from typing import Callable, Dict, List, Optional

from gitcurator.constants import MANAGED_BY_GITCURATOR
from gitcurator.core import links as _links
from gitcurator.core import dryrun as _dryrun
from gitcurator.core import website_pipeline as _wp
from gitcurator.core import note_state as _note_state

#: The log prefix the repos gate's count line carries (the twin of the
#: websites gate's BANISH_GATE_PREFIX — its own prefix so the log tells
#: which vault's door fired).
REPO_BANISH_GATE_PREFIX = "🗑️ Repo vault scan:"
#: The log prefix the repos run's tally line carries.
REPO_BANISH_TALLY_PREFIX = "🗑️ Repo run tally:"
#: The substring every repo source must carry to be this door's
#: business (defensive: the GitHub vault holds repo notes, but a stray
#: source is not a repo and never a repos banishment).
GITHUB_URL_MARK = 'github.com/'


def _is_repo_url(url: str) -> bool:
    """v0.65.0 — is this source URL a GitHub repo link (the only kind
    the repos banishment ever touches)? Cheap substring law — the same
    test the processing loop's own grammar uses."""
    return GITHUB_URL_MARK in (url or '')


def scan_pending_repo_banishments(
        vault_path: str, note_state_db=None,
        log: Optional[Callable] = None) -> Dict:
    """v0.65.0 — THE REPOS GATE'S EYES: what WOULD be banished this run.

    The detection half of the repos confirmation gate, the exact twin
    of :func:`website_pipeline.scan_pending_banishments` — both doors,
    one list, deduped by canonical URL (the repos grammar:
    ``links.normalize_url`` — trailing slash, query, fragment gone):
    the note-tag door (:func:`website_pipeline.scan_banished_notes` —
    app-owned marked repo notes only; a hand-written marked note is the
    owner's to delete by hand, counted separately) and the vault's own
    master-table door (:func:`website_pipeline.scan_decommission_table`
    — 🗑-marked rows that are not yet confirmed-stamped; a
    ``🗑️ banished — confirmed <date>`` row is history, not a pending
    ask). A URL already resting in the dismissed list (an earlier
    banishment — the burial's own DB gate) is never re-asked. Pure
    file reads + one optional DB read; no network; never raises.
    Returns ``{'items': [{'url', 'canonical', 'title', 'marker',
    'door', 'path'}], 'kept_handwritten': int}`` — the shape the
    Telegram ask and the burial both consume."""
    log = log or (lambda *a, **k: None)
    out: Dict = {'items': [], 'kept_handwritten': 0}
    if not vault_path or not os.path.isdir(vault_path):
        return out
    dismissed: set = set()
    if note_state_db is not None:
        try:
            dismissed = note_state_db.dismissed_set(
                _note_state.VAULT_GITHUB) or set()
        except Exception:
            dismissed = set()
    seen: set = set()
    for it in _wp.scan_banished_notes(vault_path, log=log):
        if not it.get('app_owned'):
            out['kept_handwritten'] += 1
            continue
        url = str(it.get('url') or '')
        if not _is_repo_url(url):
            continue        # a non-repo source in the GitHub vault —
            # not this door's business (defensive; there are none)
        canonical = _links.normalize_url(url)
        if not canonical or canonical in seen:
            continue
        if canonical in dismissed:
            continue        # an earlier banishment's own gate — the
            # note may still be mid-move (the file operation failed);
            # never re-asked, the burial retries the move silently
        seen.add(canonical)
        out['items'].append({
            'url': url, 'canonical': canonical,
            'title': os.path.splitext(os.path.basename(
                it.get('path') or ''))[0] or canonical,
            'marker': it.get('marker') or 'delete mark',
            'door': it.get('door') or 'note tag',
            'path': it.get('path') or ''})
    # the master-table door: the vault's own DECOMMISSIONED.md, the
    # same grammar the websites twin reads (a 🗑 Status on a repo row
    # is a pending ask until it is confirmed-stamped).
    try:
        table = _wp.scan_decommission_table(vault_path)
    except Exception as e:
        log(f"⚠️ Repo banish-gate table scan skipped: {e}", "warning")
        table = {}
    for url, status in (table or {}).items():
        if not _status_banished(status or ''):
            continue
        if 'confirmed' in (status or '').lower():
            continue        # history, not a pending ask
        if not _is_repo_url(url or ''):
            continue        # a websites row in this vault's table —
            # never the repos burial's call
        canonical = _links.normalize_url(url or '')
        if not canonical or canonical in seen:
            continue
        if canonical in dismissed:
            continue
        seen.add(canonical)
        try:
            owner = canonical.split(GITHUB_URL_MARK, 1)[1]
            title = owner.rstrip('/') or canonical
        except Exception:
            title = canonical
        out['items'].append({
            'url': url, 'canonical': canonical, 'title': title,
            'marker': status or '', 'door': 'master-table gesture',
            'path': ''})
    return out


def _status_banished(status: str) -> bool:
    """v0.65.0 — the table-grammar predicate (the websites twin's own
    :func:`website_pipeline._status_is_banished`, imported once so the
    grammar never forks)."""
    return bool(_wp._status_is_banished(status))


def _find_repo_note_for_url(vault_path: str, url: str,
                            cache=None,
                            log: Optional[Callable] = None) -> str:
    """v0.65.0 — the note FILE for one repo URL (the table-gesture
    burial needs it: the row names the repo, the note holds the file).

    The processed-repos ledger's row first — verified on disk (the
    app's own note: ``managed_by`` gitcurator + the same source —
    a row pointing at a stranger or at nothing is not a delivery);
    then a walk of the library for any app-owned note whose ``source``
    normalizes to the canonical (the row was lost, the note was moved
    by hand). ``''`` when nothing is found — the note is already gone
    (a hand deletion, an earlier banishment) and the burial proceeds
    DB-only. Never raises."""
    log = log or (lambda *a, **k: None)
    canonical = _links.normalize_url(url or '')
    if not canonical:
        return ''
    if cache is not None:
        try:
            row = cache.processed_row_for(canonical) or {}
            path = str(row.get('note_path') or '')
            if path and os.path.isfile(path):
                fm = _wp._parse_review_frontmatter(path)
                if fm and fm.get('managed_by', '').lower() \
                        == MANAGED_BY_GITCURATOR \
                        and _links.normalize_url(
                            fm.get('source') or '') == canonical:
                    return path
        except Exception:
            pass        # the ledger is one door, the walk is the other
    if not vault_path or not os.path.isdir(vault_path):
        return ''
    for root, dirs, files in os.walk(vault_path):
        dirs[:] = sorted(d for d in dirs
                         if d != '_inbox' and not d.startswith('.'))
        for name in sorted(files):
            if not name.lower().endswith('.md'):
                continue
            p = os.path.join(root, name)
            if not os.path.isfile(p):
                continue
            try:
                fm = _wp._parse_review_frontmatter(p)
            except Exception:
                fm = None
            if not fm or fm.get('managed_by', '').lower() \
                    != MANAGED_BY_GITCURATOR:
                continue
            if _links.normalize_url(fm.get('source') or '') != canonical:
                continue
            return p
    return ''


def _banish_repo_db(cache, note_state_db, canonical: str,
                    note_path: str, log: Optional[Callable] = None
                    ) -> Dict:
    """v0.65.0 — THE BURIAL CORE, DB first (both doors meet here: the
    note's own mark and the master table's gesture).

    The never-fetch gates BEFORE any file moves (the graveyard's law:
    they must hold even when the move below fails): the URL is
    ``dismiss``ed in note_state (§4.4 — never re-added, the batch
    loop's own skip before any GitHub API call) and ``settle_repos``ed
    (v0.63.3 — the queue door never counts it pending again), its
    ``failed_repos`` row is resolved (the retry cry, closed), its
    ``processed_repos`` row is forgotten (the ledger must not outlive
    the note). A ``note_state_db`` of None (a dry-run) skips only the
    dismissal WRITE — the rehearsal records nothing in note_state, the
    same law run_start_check follows. Returns ``{'dismissed',
    'settled', 'retry_resolved', 'row_forgotten'}``."""
    log = log or (lambda *a, **k: None)
    report = {'dismissed': False, 'settled': False,
              'retry_resolved': False, 'row_forgotten': False}
    if not canonical:
        return report
    if note_state_db is not None and not _dryrun.is_enabled():
        try:
            note_state_db.dismiss(
                _note_state.VAULT_GITHUB, canonical,
                last_path=note_path or '')
            report['dismissed'] = True
        except Exception as e:
            log(f"⚠️ Repo banishment note-state write skipped for "
                f"{canonical}: {e}", "warning")
    elif _dryrun.is_enabled():
        log(f"🗑️ {canonical}: the dismissal would be recorded (dry-run "
            f"rehearsal — note_state is never written in a rehearsal)",
            "info")
    if cache is not None:
        try:
            # the banishment is the STRONGER verdict — a leftover 404
            # quarantine row would hand the "Reset 404 Quarantine"
            # ♻️ door the power to silently un-do this burial (it
            # un-settles what it resets); the row goes first, the
            # settlement is re-written right below it.
            cache.reset_dead_links(canonical)
        except Exception:
            pass    # furniture — most banished repos were never 404s
        try:
            report['settled'] = bool(cache.settle_repos([canonical]))
        except Exception as e:
            log(f"⚠️ Repo banishment settlement skipped for "
                f"{canonical}: {e}", "warning")
        try:
            cache.resolve_failed_urls([canonical])
            report['retry_resolved'] = True
        except Exception:
            pass    # the dismissal is the gate; the queue row is furniture
        try:
            report['row_forgotten'] = bool(
                cache.forget_processed_url(canonical))
        except Exception:
            pass    # a stale row is furniture too — the gates above hold
    return report


def banish_marked_repos(cache, note_state_db, vault_path: str,
                        log: Optional[Callable] = None,
                        items: Optional[List[Dict]] = None) -> Dict:
    """v0.65.0 — THE REPOS BURIAL: enforce the owner's delete verdicts
    on the GitHub vault's repo notes (the websites
    ``banish_marked_notes`` twin).

    Every APP-OWNED repo note carrying the mark (tag / frontmatter /
    body hashtag / the Trash-folder move) — or named by a 🗑-marked row
    in the vault's own DECOMMISSIONED.md — leaves the library for
    ``.trash/banished`` and its URL is banished through every
    never-fetch gate the repos side owns (dismissed + settled + retry
    resolved + row forgotten — see :func:`_banish_repo_db`). The vault
    gains the record row (``🗑️ banished — confirmed <date>``, ♻️
    revivable). A hand-written marked note is KEPT with a warning (the
    sacred law). Idempotent (a banished note rests in ``.trash`` — the
    scan never enters dot-folders; a dismissed URL is never re-asked).
    Dry-run aware; never raises. Returns ``{'marked', 'banished',
    'notes_moved', 'kept_handwritten', 'rows_written', 'urls'}`` —
    ``urls`` is the canonical list the run's 🗑️ tally counts."""
    log = log or (lambda *a, **k: None)
    report = {'marked': 0, 'banished': 0, 'notes_moved': 0,
              'kept_handwritten': 0, 'rows_written': 0, 'urls': []}
    if not vault_path or not os.path.isdir(vault_path):
        return report
    if items is None:
        gate = scan_pending_repo_banishments(
            vault_path, note_state_db=note_state_db, log=log) or {}
        items = list(gate.get('items') or [])
        report['kept_handwritten'] = int(
            gate.get('kept_handwritten') or 0)
    if not items:
        _warn_handwritten(report['kept_handwritten'], report, log)
        return report
    date_str = datetime.now().strftime('%Y-%m-%d')
    seen: set = set()
    record_urls_tag: List[str] = []
    record_notes_tag: Dict[str, str] = {}
    record_rows_trash: List[str] = []
    record_notes_trash: Dict[str, str] = {}
    record_rows_table: List[str] = []
    record_notes_table: Dict[str, str] = {}
    for it in items:
        report['marked'] += 1
        canonical = _links.normalize_url(it.get('url') or
                                         it.get('canonical') or '')
        if not canonical or canonical in seen:
            continue            # two marked notes, one repo — one burial
        seen.add(canonical)
        door = str(it.get('door') or 'note tag')
        via_trash = (door == 'trash folder')
        via_table = (door == 'master-table gesture')
        note_path = str(it.get('path') or '')
        if via_table and not note_path:
            note_path = _find_repo_note_for_url(
                vault_path, canonical, cache=cache, log=log)
        b = _banish_repo_db(cache, note_state_db, canonical,
                            note_path, log=log)
        report['banished'] += 1
        if note_path and os.path.isfile(note_path):
            dst = _wp._banish_note_file(vault_path, note_path, log=log)
            if dst:
                report['notes_moved'] += 1
        elif not _dryrun.is_enabled():
            log(f"🗑️ {canonical}: banished — the note file was already "
                f"gone (a hand deletion, an earlier banishment); the "
                f"repo is dismissed, settled and row-forgotten — never "
                f"fetched or counted again", "info")
        if via_trash:
            record_rows_trash.append(canonical)
            record_notes_trash[canonical] = \
                "🗑️ you moved it to the Trash folder (repo vault)"
        elif via_table:
            record_rows_table.append(canonical)
            record_notes_table[canonical] = \
                f"🗑️ marked in this table: {it.get('marker') or 'delete'}"
        else:
            record_urls_tag.append(canonical)
            record_notes_tag[canonical] = \
                f"🗑️ marked on the repo note: " \
                f"{it.get('marker') or 'delete'}"
        if not _dryrun.is_enabled():
            log(f"🗑️ {canonical}: banished — the repo note left the "
                f"library for "
                f"{_wp.BANISH_QUARANTINE_RELPATH.replace(os.sep, '/')} "
                f"and the repo is dismissed + settled — never fetched, "
                f"never counted, never retried again (♻️ in this table "
                f"revives it)", "info")
    if seen:
        try:
            written = 0
            if record_urls_tag:
                written += _wp.write_decommission_candidates(
                    vault_path, record_urls_tag, source='repo note tag',
                    notes=record_notes_tag,
                    status=f"🗑️ banished — confirmed {date_str}",
                    log=log)
            if record_rows_trash:
                written += _wp.write_decommission_candidates(
                    vault_path, record_rows_trash,
                    source='repo Trash folder',
                    notes=record_notes_trash,
                    status=f"🗑️ banished — confirmed {date_str}",
                    log=log)
            if record_rows_table:
                written += _wp.write_decommission_candidates(
                    vault_path, record_rows_table,
                    source='repo table gesture',
                    notes=record_notes_table,
                    status=f"🗑️ banished — confirmed {date_str}",
                    log=log)
            report['rows_written'] = written
        except Exception as e:
            log(f"⚠️ Repo banishment record rows skipped: {e}",
                "warning")
    report['urls'] = sorted(seen)
    _warn_handwritten(report['kept_handwritten'], report, log)
    if report['banished']:
        log(f"🗑️ Repos banishment: {report['banished']} repo note(s) "
            f"removed from the library and banished — dismissed, "
            f"settled, retry-resolved, row-forgotten (never fetched, "
            f"never counted, never retried again); "
            f"{report['kept_handwritten']} hand-written marked note(s) "
            f"kept (yours); the vault's table holds the record rows "
            f"(♻️ revivable)", "info")
    return report


def _warn_handwritten(kept: int, report: Dict,
                      log: Callable) -> None:
    """v0.65.0 — the sacred law's own line: marked notes that are NOT
    the app's are counted and kept, never deleted by this door."""
    if kept:
        log(f"✍️ kept {kept} marked note(s) — hand-written (yours); the "
            f"app never deletes what it did not write — delete them by "
            f"hand in Obsidian if you want them gone", "warning")


def revive_banished_repos(cache, note_state_db, vault_path: str,
                          log: Optional[Callable] = None) -> int:
    """v0.65.0 — THE ♻️ DOOR BACK (the websites master table's revive
    twin, on the GitHub vault's own table).

    Every ♻-marked row in the vault's DECOMMISSIONED.md (:func:
    ``website_pipeline._status_is_revived`` — ♻️ / revived / restored /
    un-decommissioned) revives its repo: the note_state dismissal is
    cleared (the §4.4 gate opens) and the settled ledger row is
    removed (v0.63.3 — fetched like new again). A revival is the
    owner's explicit hand — this pass runs BEFORE the confirmation
    gate, whatever the gate's verdict (the same law the websites side
    keeps for dead/reviewed rows). Idempotent — a second run finds the
    same ♻️ row but nothing left to clear, and stays silent. Pure file
    read + two DB writes; never raises; returns how many repos were
    actually revived (a DB row moved)."""
    log = log or (lambda *a, **k: None)
    if not vault_path or not os.path.isdir(vault_path):
        return 0
    try:
        table = _wp.scan_decommission_table(vault_path) or {}
    except Exception:
        return 0
    revived = 0
    for url, status in table.items():
        if not _wp._status_is_revived(status or ''):
            continue
        if not _is_repo_url(url or ''):
            continue        # a websites row — not this door's call
        canonical = _links.normalize_url(url or '')
        if not canonical:
            continue
        parts = []
        try:
            if note_state_db is not None \
                    and not _dryrun.is_enabled() and \
                    note_state_db.clear_dismissed(
                        _note_state.VAULT_GITHUB, canonical):
                parts.append("the dismissal is gone")
        except Exception:
            pass    # bookkeeping never fails a revival
        try:
            if cache is not None and cache.unsettle_repo(canonical):
                parts.append("the settlement is gone")
        except Exception:
            pass
        if parts:
            revived += 1
            log(f"♻️ {canonical}: revived — "
                f"{' and '.join(parts)}, it will be fetched like new",
                "info")
    return revived


def run_repo_banishment_pass(cache, note_state_db, vault_path: str,
                             log: Optional[Callable] = None,
                             config: Optional[dict] = None,
                             banish_confirm_factory=None) -> Dict:
    """v0.65.0 — THE REPOS BANISHMENT PASS, one orchestration (the
    ``WebsitePipeline.__init__`` gate's twin, run at the start of every
    GitHub batch — ProcessingWorker.run, which the GUI, the CLI and
    headless all drive):

    1. the ♻️ revive pass (:func:`revive_banished_repos`) — the
       owner's explicit hand, runs first, ungated;
    2. the detection scan (:func:`scan_pending_repo_banishments`) —
       both doors, deduped, hand-written marks counted but never
       asked;
    3. the count SPOKEN first ("so I ensure that system successfully
       detected them" — the owner's own words), then the ask over the
       INJECTED channel factory (``banish_confirm_factory`` builds the
       Telegram round-trip only when there is something to ask — the
       hermetic law: this module never imports the network itself);
    4. the verdict gates the burial: 'confirmed' (and 'auto' — the
       config ``banish_confirm: false`` opt-out) ->
       :func:`banish_marked_repos`; 'declined' / 'timeout' / 'defer'
       -> the marks and the notes stay, the next run asks again;
    5. THE TALLY, spoken every run (the zero run answers too — 0 is a
       number).

    Returns ``{'verdict', 'pending', 'banished', 'urls', 'revived'}``
    — the caller (the batch summary) may ride it. Never raises; a
    broken scan is one warning line and a defer."""
    log = log or (lambda *a, **k: None)
    out: Dict = {'verdict': 'auto', 'pending': 0, 'banished': 0,
                 'urls': [], 'revived': 0}
    if not vault_path or not os.path.isdir(vault_path):
        return out
    # 1 — the ♻️ door back, the owner's explicit hand, ungated.
    try:
        out['revived'] = revive_banished_repos(
            cache, note_state_db, vault_path, log=log)
    except Exception as e:
        log(f"⚠️ Repo revival pass skipped: {e}", "warning")
    # 2 — the gate's eyes.
    try:
        gate = scan_pending_repo_banishments(
            vault_path, note_state_db=note_state_db, log=log) or {}
    except Exception as e:
        log(f"⚠️ Repo banish-gate scan skipped: {e}", "warning")
        gate = {}
    items = list(gate.get('items') or [])
    pending = len(items)
    out['pending'] = pending
    if not pending:
        return out        # the cheap no-op — most runs
    # 3 — the number FIRST, then the ask.
    log(f"{REPO_BANISH_GATE_PREFIX} {pending} repo note(s) marked for "
        f"deletion (🗑️ / delete / auto_delete) — asking you to confirm "
        f"before anything is removed", "info")
    verdict = 'defer'
    channel = None
    if banish_confirm_factory is not None:
        try:
            channel = banish_confirm_factory()
        except Exception as e:
            log(f"⚠️ Repo banish gate channel unavailable: {e} — "
                f"nothing deleted, the marks stay; I'll ask again next "
                f"run", "warning")
            channel = None
    if channel is not None:
        try:
            res = channel(items, log)
        except Exception as e:
            log(f"⚠️ Repo banish confirmation failed: {e} — nothing "
                f"deleted, the marks stay", "warning")
            res = None
        if isinstance(res, dict):
            verdict = str(res.get('verdict') or 'defer')
        elif isinstance(res, str) and res:
            verdict = res
    elif config is not None and str(
            config.get('banish_confirm', True)).strip().lower() \
            in ('false', '0', 'no', 'off'):
        verdict = 'auto'    # the owner's explicit opt-out
    else:
        # no channel injected and no opt-out — the SAFE default: the
        # marks stay, the next run asks again.
        verdict = 'defer'
        log(f"⚠️ {pending} marked repo note(s) found, but no "
            f"confirmation channel is reachable (the Telegram worker is "
            f"not paired/enabled) — nothing deleted; the marks stay, "
            f"I'll ask again next run (config \"banish_confirm\": false "
            f"restores the old auto-delete)", "warning")
    out['verdict'] = verdict
    if verdict == 'confirmed':
        log(f"✅ You confirmed the deletion — the {pending} marked repo "
            f"note(s) leave the GitHub vault now; the repos are "
            f"dismissed + settled (never fetched, never counted, never "
            f"retried again); GitHub drops them on the next seal",
            "info")
    elif verdict == 'declined':
        log(f"👌 You kept them — the {pending} marked repo note(s) stay "
            f"in the vault; the marks stay too, I'll ask again next "
            f"run", "info")
    elif verdict == 'timeout':
        log(f"⌛ No answer in time — the {pending} marked repo note(s) "
            f"stay; the marks stay, I'll ask again next run", "info")
    # 4 — the burial (the gate's hold: only confirmed / auto).
    if verdict in ('auto', 'confirmed'):
        try:
            report = banish_marked_repos(
                cache, note_state_db, vault_path, log=log,
                items=items) or {}
            out['urls'] = list(report.get('urls') or [])
            out['banished'] = len(out['urls'])
        except Exception as e:
            log(f"⚠️ Repo banishment pass skipped: {e}", "warning")
    # 5 — THE TALLY (the zero run answers too).
    if out['banished']:
        log(f"{REPO_BANISH_TALLY_PREFIX} {out['banished']} repo(s) "
            f"removed this run and never fetched again — you marked "
            f"their notes for deletion (🗑️ / delete / auto_delete); the "
            f"notes rest in "
            f"{_wp.BANISH_QUARANTINE_RELPATH.replace(os.sep, '/')}, the "
            f"repos are dismissed + settled, and ♻️ on the record row "
            f"undoes any of them", "info")
    elif pending and verdict != 'auto':
        why = {'declined': "you said keep",
               'timeout': "no answer in time",
               'defer': "no confirmation channel"}.get(verdict, verdict)
        log(f"{REPO_BANISH_TALLY_PREFIX} 0 repos removed this run — "
            f"{pending} marked repo note(s) KEPT ({why}; the marks stay, "
            f"I'll ask again next run)", "info")
    return out

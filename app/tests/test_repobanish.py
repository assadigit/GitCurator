"""tests/test_repobanish.py — v0.65.0, THE REPOS BANISHMENT: the same
mechanism the websites own, activated for the GitHub projects vault.

The owner's ask (session, verbatim): "The same mechanism which we
created for websites to banish theme, delete from vault and never
fetch again, i want the same mechanism to be activated for github
projects vault. so for example i can banish an already processed
github repo, so it never fetches and processed."

Covered here (zero network — the temp-vault hermetic pattern; the GUI
wiring is pinned by source contracts, the test_banishment /
test_reposettled house style):

* THE MARKS, read by the same eyes — a repo note tagged 🗑️ (frontmatter
  flow AND block style), a true ``decommission:`` frontmatter key, an
  ``#auto-delete`` hashtag typed in the note BODY, and the Trash-folder
  MOVE (the owner's gesture, v0.62.0's door, free on this vault too).
  A hand-written marked note is counted and KEPT (the sacred law). A
  non-repo source in the GitHub vault is never this door's business. A
  note already resting in ``.trash/banished`` is never re-found.
* THE GATE'S EYES — ``scan_pending_repo_banishments``: both doors (the
  note marks + the vault's own DECOMMISSIONED.md table gestures), one
  deduped list keyed by ``links.normalize_url`` (the trailing-slash
  twin is ONE ask), already-dismissed URLs never re-asked, a
  confirmed-stamped row is history not a pending ask, a ♻️ row is a
  revival not a banishment.
* THE BURIAL — ``banish_marked_repos``: the note file leaves the
  library for ``.trash/banished``; the repo is DISMISSED in note_state
  (§4.4 — never re-added) AND SETTLED in the repos ledger (v0.63.3 —
  the queue door never counts it pending again); its failed row is
  resolved (no retry cry); its processed row is forgotten (the ledger
  must not outlive the note); the record row lands in the vault's own
  DECOMMISSIONED.md (♻️ revivable). Idempotent; a hand-written marked
  note is KEPT; a table-gesture row whose note is ALREADY gone is
  banished DB-only (the silent-deletion loop, closed by the explicit
  verdict); a leftover 404-quarantine row is subsumed (the banishment
  is the stronger verdict — the quarantine reset can never un-do it);
  dry-run moves nothing and records no dismissal.
* THE ♻️ DOOR BACK — ``revive_banished_repos``: a ♻️ row clears the
  dismissal and un-settles the repo (fetched like new); idempotent; a
  websites ♻️ row in this vault's table is never the repos call.
* THE PASS — ``run_repo_banishment_pass``: the revival runs first
  ungated (the owner's explicit hand); the count is spoken BEFORE the
  ask; the ask rides the injected channel factory (built ONLY when
  there are marks — a clean vault never pays the handshake); confirmed
  -> the burial + the tally; declined / timeout / no-channel -> the
  marks and the notes stay, the next run asks again; config
  ``banish_confirm: false`` restores the auto reflex; a broken
  environment is one warning and a defer, never a raise.
* THE NEVER-FETCH PROMISE — the loop, closed: after a burial the
  batch-loop gates hold (the dismissed skip AND the settled skip both
  fire for a force-fed link); the queue-door classification counts a
  banished repo settled (never pending); the CacheDB helpers
  (``processed_row_for`` / ``forget_processed_url``) keep their
  contracts.
* THE NOTE TEACHES THE DOOR — build_note's retire hint names the
  gestures and the promise.

No PyQt import at module level (the libEGL-less sandbox rule) — the
worker wiring is pinned by a source contract; the batch loop's gates
are exercised through their own load paths.
"""

import os
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

_REPO_ROOT = os.path.dirname(_APP_ROOT)

from gitcurator.core import dryrun
from gitcurator.core import links as _links
from gitcurator.core import note_state as _note_state
from gitcurator.core import note_builder as _note_builder
from gitcurator.core import repo_banish
from gitcurator.core import website_pipeline as _wp
from gitcurator.gui.cache_db import CacheDB


# ---------------------------------------------------------------------------
# Hermetic helpers (the test_banishment / test_reposettled patterns)
# ---------------------------------------------------------------------------

def _repo_note(vault, rel, url, tags='[cli]', managed='gitcurator',
               body=None, extra_fm=''):
    """Hand-write a repo note the way build_note shapes it (the
    frontmatter the banishment grammar reads: source + managed_by +
    tags)."""
    path = os.path.join(vault, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"""---
source: {url}
aliases: []
tags: {tags}
category: "Agents"
date_processed: 2026-10-09
managed_by: "{managed}"
{extra_fm}---
# {url.split('/')[-1]}

{body or 'A curated repo note. ' + 'x' * 200}

---
*Source: [GitHub]({url})*
""")
    return path


def _mark(path, marker='🗑️'):
    """The owner's hand: add the delete mark to a note's tags line."""
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    if 'tags: [' in text:
        text = text.replace('tags: [', f'tags: [{marker}, ', 1)
    else:       # block style (Obsidian's property editor)
        text = text.replace('tags:\n', f'tags:\n  - {marker}\n', 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


def _table(vault, rows):
    """Write the vault's own DECOMMISSIONED.md with the given data rows
    (the shape write_decommission_candidates produces; ``rows`` is a
    list of (icon, url, status) tuples)."""
    path = os.path.join(vault, '_review', 'DECOMMISSIONED.md')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = [
        "# Review Master Table — decommission or approve", "",
        "| # | Date | URL | Domain | Source | Status | Notes |",
        "|---|------|-----|--------|--------|--------|-------|",
    ]
    for icon, url, status in rows:
        domain = url.split('//', 1)[-1].split('/', 1)[0]
        lines.append(f"| {icon} | 2026-10-09 | {url} | {domain} "
                     f"| test | {status} | |")
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return path


class _Recorder:
    """A log sink that keeps every line + level for the assertions."""

    def __init__(self):
        self.lines = []

    def __call__(self, msg, level='info'):
        self.lines.append((str(msg), level))

    def joined(self):
        return '\n'.join(m for m, _ in self.lines)


class _Channel:
    """The injected banish_confirm channel (the Telegram round-trip's
    stand-in): records the items it was asked, answers the verdict the
    test scripts."""

    def __init__(self, verdict='confirmed'):
        self.verdict = verdict
        self.asks = []

    def __call__(self, items, log=None):
        self.asks.append(list(items or []))
        return {'verdict': self.verdict, 'report': None}


# ---------------------------------------------------------------------------
# 1. The gate's eyes
# ---------------------------------------------------------------------------

class TestScanPending(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='repobanish-scan-')
        self.vault = os.path.join(self.tmp, 'GitHubVault')
        os.makedirs(os.path.join(self.vault, 'AI-Domain'), exist_ok=True)
        self.log = _Recorder()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _scan(self, note_state_db=None):
        return repo_banish.scan_pending_repo_banishments(
            self.vault, note_state_db=note_state_db, log=self.log)

    def test_marked_repo_note_is_one_pending_item(self):
        p = _repo_note(self.vault, 'AI-Domain/agent.md',
                       'https://github.com/owner/agent')
        _mark(p)
        out = self._scan()
        self.assertEqual(len(out['items']), 1)
        it = out['items'][0]
        self.assertEqual(it['canonical'],
                         'https://github.com/owner/agent')
        self.assertEqual(it['door'], 'note tag')
        self.assertEqual(it['path'], p)
        self.assertEqual(out['kept_handwritten'], 0)

    def test_body_hashtag_and_frontmatter_key_fire(self):
        p1 = _repo_note(self.vault, 'AI-Domain/one.md',
                        'https://github.com/owner/one',
                        body='uses agents #auto-delete')
        p2 = _repo_note(self.vault, 'AI-Domain/two.md',
                        'https://github.com/owner/two',
                        extra_fm='decommission: true\n')
        out = self._scan()
        self.assertEqual({i['canonical'] for i in out['items']},
                         {'https://github.com/owner/one',
                          'https://github.com/owner/two'})

    def test_block_style_tags_and_auto_delete_word_fire(self):
        p = _repo_note(self.vault, 'AI-Domain/block.md',
                       'https://github.com/owner/block',
                       tags='')
        with open(p, 'r', encoding='utf-8') as f:
            text = f.read()
        text = text.replace('tags:', 'tags:\n  - auto_delete', 1)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(text)
        out = self._scan()
        self.assertEqual(len(out['items']), 1)
        self.assertIn('auto_delete', out['items'][0]['marker'])

    def test_trash_folder_move_is_the_verdict(self):
        p = _repo_note(self.vault, 'Trash/moved.md',
                       'https://github.com/owner/moved')
        out = self._scan()
        self.assertEqual(len(out['items']), 1)
        self.assertEqual(out['items'][0]['door'], 'trash folder')

    def test_handwritten_marked_note_is_kept_not_asked(self):
        p = _repo_note(self.vault, 'AI-Domain/human.md',
                       'https://github.com/owner/human',
                       managed='human')
        _mark(p)
        out = self._scan()
        self.assertEqual(out['items'], [])
        self.assertEqual(out['kept_handwritten'], 1)

    def test_non_github_source_is_not_this_doors_business(self):
        p = _repo_note(self.vault, 'AI-Domain/site.md',
                       'https://cleanup.pictures/')
        _mark(p)
        out = self._scan()
        # defensive: an app-owned non-repo source never fires (there
        # are none in a real GitHub vault) and never counts as kept
        self.assertEqual(out['items'], [])
        self.assertEqual(out['kept_handwritten'], 0)

    def test_trailing_slash_twin_is_one_ask(self):
        p1 = _repo_note(self.vault, 'AI-Domain/a.md',
                        'https://github.com/owner/twin')
        p2 = _repo_note(self.vault, 'AI-Domain/b.md',
                        'https://github.com/owner/twin/')
        _mark(p1)
        _mark(p2)
        out = self._scan()
        self.assertEqual(len(out['items']), 1)
        self.assertEqual(out['items'][0]['canonical'],
                         'https://github.com/owner/twin')

    def test_already_dismissed_is_never_re_asked(self):
        p = _repo_note(self.vault, 'AI-Domain/gone.md',
                       'https://github.com/owner/gone')
        _mark(p)
        db = _note_state.NoteStateDB(
            os.path.join(self.tmp, 'cache.db'))
        try:
            db.dismiss(_note_state.VAULT_GITHUB,
                       'https://github.com/owner/gone', p)
            out = self._scan(note_state_db=db)
            self.assertEqual(out['items'], [])
        finally:
            db.close()

    def test_note_in_trash_quarantine_never_refound(self):
        p = _repo_note(self.vault, '.trash/banished/resting.md',
                       'https://github.com/owner/resting')
        _mark(p)
        out = self._scan()
        self.assertEqual(out['items'], [])

    def test_table_gesture_is_a_pending_ask(self):
        _table(self.vault, [('🗑️', 'https://github.com/owner/row',
                             'delete')])
        out = self._scan()
        self.assertEqual(len(out['items']), 1)
        self.assertEqual(out['items'][0]['door'], 'master-table gesture')
        self.assertEqual(out['items'][0]['canonical'],
                         'https://github.com/owner/row')

    def test_confirmed_row_is_history_not_an_ask(self):
        _table(self.vault, [('🗑️', 'https://github.com/owner/done',
                             '🗑️ banished — confirmed 2026-10-09')])
        out = self._scan()
        self.assertEqual(out['items'], [])

    def test_revived_row_is_not_a_banishment_ask(self):
        _table(self.vault, [('♻️', 'https://github.com/owner/back',
                             'revived')])
        out = self._scan()
        self.assertEqual(out['items'], [])

    def test_websites_row_in_this_vault_table_is_ignored(self):
        _table(self.vault, [('🗑️', 'https://cleanup.pictures/',
                             'delete')])
        out = self._scan()
        self.assertEqual(out['items'], [])

    def test_missing_vault_is_quiet(self):
        out = repo_banish.scan_pending_repo_banishments(
            os.path.join(self.tmp, 'nope'), log=self.log)
        self.assertEqual(out, {'items': [], 'kept_handwritten': 0})


# ---------------------------------------------------------------------------
# 2. The burial
# ---------------------------------------------------------------------------

class TestTheBurial(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='repobanish-bury-')
        self.vault = os.path.join(self.tmp, 'GitHubVault')
        os.makedirs(os.path.join(self.vault, 'AI-Domain'), exist_ok=True)
        self.cache = CacheDB(os.path.join(self.tmp, 'cache.db'))
        self.ns = _note_state.NoteStateDB(
            os.path.join(self.tmp, 'cache.db'))
        self.log = _Recorder()

    def tearDown(self):
        self.cache.close()
        self.ns.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _bury(self, items=None):
        return repo_banish.banish_marked_repos(
            self.cache, self.ns, self.vault, log=self.log, items=items)

    def test_the_full_burial_every_gate_holds(self):
        p = _repo_note(self.vault, 'AI-Domain/agent.md',
                       'https://github.com/owner/agent')
        _mark(p)
        self.cache.add_processed(
            1, 'https://github.com/owner/agent', 'owner', 'agent', p,
            'Agents')
        self.cache.add_failed('https://github.com/owner/agent', 'boom')
        rep = self._bury()
        # the note left the library for .trash/banished
        self.assertFalse(os.path.exists(p))
        self.assertTrue(os.path.isfile(os.path.join(
            self.vault, '.trash', 'banished', 'agent.md')))
        # DB first: dismissed + settled + retry resolved + row forgotten
        self.assertTrue(self.ns.is_dismissed(
            _note_state.VAULT_GITHUB, 'https://github.com/owner/agent'))
        self.assertTrue(self.cache.is_repo_settled(
            'https://github.com/owner/agent'))
        self.assertFalse(self.cache.get_failed_urls())
        self.assertIsNone(self.cache.processed_row_for(
            'https://github.com/owner/agent'))
        # the report + the record row
        self.assertEqual(rep['banished'], 1)
        self.assertEqual(rep['notes_moved'], 1)
        self.assertEqual(rep['urls'], ['https://github.com/owner/agent'])
        self.assertEqual(rep['rows_written'], 1)
        tbl = _wp.scan_decommission_table(self.vault)
        self.assertIn('https://github.com/owner/agent', tbl)
        self.assertIn('confirmed', tbl['https://github.com/owner/agent'])
        # the burial's own summary line speaks the promise
        self.assertIn('never fetched', self.log.joined())
        self.assertIn('row-forgotten', self.log.joined())

    def test_idempotent_second_run_banishes_nothing(self):
        p = _repo_note(self.vault, 'AI-Domain/agent.md',
                       'https://github.com/owner/agent')
        _mark(p)
        self._bury()
        rep2 = self._bury()
        self.assertEqual(rep2['banished'], 0)
        self.assertEqual(rep2['urls'], [])

    def test_handwritten_marked_note_is_kept(self):
        p = _repo_note(self.vault, 'AI-Domain/human.md',
                       'https://github.com/owner/human',
                       managed='human')
        _mark(p)
        rep = self._bury()
        self.assertEqual(rep['banished'], 0)
        self.assertTrue(os.path.exists(p))
        self.assertIn('never deletes what it did not write',
                      self.log.joined())

    def test_table_gesture_with_note_removes_the_note(self):
        p = _repo_note(self.vault, 'AI-Domain/row.md',
                       'https://github.com/owner/row')
        self.cache.add_processed(
            2, 'https://github.com/owner/row', 'owner', 'row', p, 'Agents')
        _table(self.vault, [('🗑️', 'https://github.com/owner/row',
                             'delete')])
        gate = repo_banish.scan_pending_repo_banishments(
            self.vault, note_state_db=self.ns, log=self.log)
        rep = self._bury(items=gate['items'])
        self.assertFalse(os.path.exists(p))
        self.assertTrue(self.cache.is_repo_settled(
            'https://github.com/owner/row'))
        self.assertTrue(self.ns.is_dismissed(
            _note_state.VAULT_GITHUB, 'https://github.com/owner/row'))
        self.assertIsNone(self.cache.processed_row_for(
            'https://github.com/owner/row'))

    def test_table_gesture_note_already_gone_is_db_only(self):
        # the owner deleted the file by hand and marked the row — his
        # exact workaround; the burial closes the loop DB-only
        _table(self.vault, [('🗑️', 'https://github.com/owner/gone',
                             'delete')])
        gate = repo_banish.scan_pending_repo_banishments(
            self.vault, note_state_db=self.ns, log=self.log)
        rep = self._bury(items=gate['items'])
        self.assertEqual(rep['banished'], 1)
        self.assertEqual(rep['notes_moved'], 0)
        self.assertTrue(self.ns.is_dismissed(
            _note_state.VAULT_GITHUB, 'https://github.com/owner/gone'))
        self.assertTrue(self.cache.is_repo_settled(
            'https://github.com/owner/gone'))
        self.assertIn('already gone', self.log.joined())

    def test_leftover_quarantine_row_is_subsumed(self):
        p = _repo_note(self.vault, 'AI-Domain/quar.md',
                       'https://github.com/owner/quar')
        _mark(p)
        self.cache.decommission('https://github.com/owner/quar', '404')
        self._bury()
        # the banishment is the STRONGER verdict: the quarantine row is
        # gone (its ♻️ reset can never un-do this burial) and the repo
        # stays settled
        self.assertFalse(self.cache.is_decommissioned(
            'https://github.com/owner/quar'))
        self.assertTrue(self.cache.is_repo_settled(
            'https://github.com/owner/quar'))
        self.assertTrue(self.ns.is_dismissed(
            _note_state.VAULT_GITHUB, 'https://github.com/owner/quar'))

    def test_dry_run_moves_nothing_and_records_no_dismissal(self):
        p = _repo_note(self.vault, 'AI-Domain/dry.md',
                       'https://github.com/owner/dry')
        _mark(p)
        self.cache.add_processed(
            3, 'https://github.com/owner/dry', 'owner', 'dry', p, 'Agents')
        dryrun.enable()
        try:
            rep = self._bury()
        finally:
            dryrun.disable()
            dryrun.clear()
        self.assertTrue(os.path.exists(p))     # the note stays
        self.assertFalse(self.ns.is_dismissed(   # note_state untouched
            _note_state.VAULT_GITHUB, 'https://github.com/owner/dry'))
        self.assertEqual(rep['banished'], 1)   # the rehearsal counts
        self.assertIn('rehearsal', self.log.joined())

    def test_missing_vault_is_quiet(self):
        rep = repo_banish.banish_marked_repos(
            self.cache, self.ns, os.path.join(self.tmp, 'nope'),
            log=self.log)
        self.assertEqual(rep['banished'], 0)


# ---------------------------------------------------------------------------
# 3. The ♻️ door back
# ---------------------------------------------------------------------------

class TestTheRevive(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='repobanish-revive-')
        self.vault = os.path.join(self.tmp, 'GitHubVault')
        os.makedirs(self.vault, exist_ok=True)
        self.cache = CacheDB(os.path.join(self.tmp, 'cache.db'))
        self.ns = _note_state.NoteStateDB(
            os.path.join(self.tmp, 'cache.db'))
        self.log = _Recorder()

    def tearDown(self):
        self.cache.close()
        self.ns.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_revive_clears_dismissal_and_settlement(self):
        url = 'https://github.com/owner/back'
        self.ns.dismiss(_note_state.VAULT_GITHUB, url, 'note.md')
        self.cache.settle_repos([url])
        _table(self.vault, [('♻️', url, 'revived')])
        n = repo_banish.revive_banished_repos(
            self.cache, self.ns, self.vault, log=self.log)
        self.assertEqual(n, 1)
        self.assertFalse(self.ns.is_dismissed(
            _note_state.VAULT_GITHUB, url))
        self.assertFalse(self.cache.is_repo_settled(url))
        self.assertIn('fetched like new', self.log.joined())

    def test_revive_is_idempotent(self):
        url = 'https://github.com/owner/back'
        self.ns.dismiss(_note_state.VAULT_GITHUB, url, 'note.md')
        self.cache.settle_repos([url])
        _table(self.vault, [('♻️', url, 'revived')])
        repo_banish.revive_banished_repos(
            self.cache, self.ns, self.vault, log=self.log)
        n2 = repo_banish.revive_banished_repos(
            self.cache, self.ns, self.vault, log=self.log)
        self.assertEqual(n2, 0)

    def test_websites_revive_row_is_ignored(self):
        self.cache.settle_repos(['https://cleanup.pictures/'])
        _table(self.vault, [('♻️', 'https://cleanup.pictures/',
                             'revived')])
        n = repo_banish.revive_banished_repos(
            self.cache, self.ns, self.vault, log=self.log)
        self.assertEqual(n, 0)
        self.assertTrue(self.cache.is_repo_settled(
            'https://cleanup.pictures/'))

    def test_no_table_is_quiet(self):
        n = repo_banish.revive_banished_repos(
            self.cache, self.ns, self.vault, log=self.log)
        self.assertEqual(n, 0)


# ---------------------------------------------------------------------------
# 4. The pass (the gate's orchestration)
# ---------------------------------------------------------------------------

class TestThePass(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='repobanish-pass-')
        self.vault = os.path.join(self.tmp, 'GitHubVault')
        os.makedirs(os.path.join(self.vault, 'AI-Domain'), exist_ok=True)
        self.cache = CacheDB(os.path.join(self.tmp, 'cache.db'))
        self.ns = _note_state.NoteStateDB(
            os.path.join(self.tmp, 'cache.db'))
        self.log = _Recorder()

    def tearDown(self):
        self.cache.close()
        self.ns.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, factory=None, config=None):
        return repo_banish.run_repo_banishment_pass(
            self.cache, self.ns, self.vault, log=self.log,
            config=config, banish_confirm_factory=factory)

    def _marked(self, url='https://github.com/owner/agent'):
        p = _repo_note(self.vault, 'AI-Domain/agent.md', url)
        _mark(p)
        return p

    def test_clean_vault_is_a_quiet_noop(self):
        built = []
        out = self._run(factory=lambda: built.append(1) or _Channel())
        self.assertEqual(out['pending'], 0)
        self.assertEqual(out['verdict'], 'auto')
        self.assertEqual(built, [])     # the channel is never built
        self.assertEqual(self.log.joined(), '')

    def test_count_spoken_before_the_ask(self):
        self._marked()
        ch = _Channel('confirmed')
        self._run(factory=lambda: ch)
        j = self.log.joined()
        self.assertIn('1 repo note(s) marked for deletion', j)
        self.assertEqual(len(ch.asks), 1)
        self.assertEqual(ch.asks[0][0]['url'],
                         'https://github.com/owner/agent')
        self.assertEqual(ch.asks[0][0]['door'], 'note tag')

    def test_confirmed_buries_and_tallies(self):
        p = self._marked()
        out = self._run(factory=lambda: _Channel('confirmed'))
        self.assertEqual(out['verdict'], 'confirmed')
        self.assertEqual(out['banished'], 1)
        self.assertFalse(os.path.exists(p))
        self.assertIn('Repo run tally: 1 repo(s) removed', self.log.joined())

    def test_declined_keeps_marks_and_notes(self):
        p = self._marked()
        out = self._run(factory=lambda: _Channel('declined'))
        self.assertEqual(out['verdict'], 'declined')
        self.assertEqual(out['banished'], 0)
        self.assertTrue(os.path.exists(p))
        self.assertFalse(self.ns.is_dismissed(
            _note_state.VAULT_GITHUB, 'https://github.com/owner/agent'))
        self.assertIn('KEPT', self.log.joined())

    def test_timeout_keeps_marks_and_notes(self):
        p = self._marked()
        out = self._run(factory=lambda: _Channel('timeout'))
        self.assertEqual(out['verdict'], 'timeout')
        self.assertTrue(os.path.exists(p))

    def test_no_channel_defers_with_the_honest_warning(self):
        self._marked()
        out = self._run()
        self.assertEqual(out['verdict'], 'defer')
        self.assertEqual(out['banished'], 0)
        self.assertIn('no confirmation channel', self.log.joined())

    def test_config_optout_restores_the_auto_reflex(self):
        p = self._marked()
        out = self._run(config={'banish_confirm': False})
        self.assertEqual(out['verdict'], 'auto')
        self.assertEqual(out['banished'], 1)
        self.assertFalse(os.path.exists(p))

    def test_revival_runs_before_the_gate_whatever_the_verdict(self):
        url = 'https://github.com/owner/back'
        self.ns.dismiss(_note_state.VAULT_GITHUB, url, '')
        self.cache.settle_repos([url])
        _table(self.vault, [('♻️', url, 'revived')])
        self._marked()
        out = self._run(factory=lambda: _Channel('declined'))
        # the revival fired even though the gate declined
        self.assertEqual(out['revived'], 1)
        self.assertFalse(self.cache.is_repo_settled(url))

    def test_broken_channel_is_a_defer_never_a_raise(self):
        self._marked()

        def factory():
            raise RuntimeError('boom')

        out = self._run(factory=factory)
        self.assertEqual(out['verdict'], 'defer')
        self.assertIn('channel unavailable', self.log.joined())

    def test_broken_scan_never_raises(self):
        # a vault path that is a FILE, not a dir — the walk stays quiet
        fp = os.path.join(self.tmp, 'notadir')
        with open(fp, 'w', encoding='utf-8') as f:
            f.write('x')
        out = repo_banish.run_repo_banishment_pass(
            self.cache, self.ns, fp, log=self.log)
        self.assertEqual(out['pending'], 0)


# ---------------------------------------------------------------------------
# 5. The never-fetch promise (the loop, closed)
# ---------------------------------------------------------------------------

class TestThePromise(unittest.TestCase):
    """After a burial, every gate the repos side owns holds."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='repobanish-promise-')
        self.vault = os.path.join(self.tmp, 'GitHubVault')
        os.makedirs(os.path.join(self.vault, 'AI-Domain'), exist_ok=True)
        self.cache = CacheDB(os.path.join(self.tmp, 'cache.db'))
        self.ns = _note_state.NoteStateDB(
            os.path.join(self.tmp, 'cache.db'))
        self.log = _Recorder()

    def tearDown(self):
        self.cache.close()
        self.ns.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_batch_loop_gates_dismissed_and_settled_both_hold(self):
        url = 'https://github.com/owner/agent'
        p = _repo_note(self.vault, 'AI-Domain/agent.md', url)
        _mark(p)
        repo_banish.banish_marked_repos(
            self.cache, self.ns, self.vault, log=self.log)
        # the batch loop's own loads (the exact shapes the worker reads)
        dismissed = self.ns.dismissed_set(_note_state.VAULT_GITHUB)
        settled = self.cache.get_settled_repo_set()
        norm = _links.normalize_url(url)
        self.assertIn(norm, dismissed)   # the §4.4 skip fires
        self.assertIn(norm, settled)     # the settled skip fires too
        # the retry queue has nothing to cry about
        self.assertFalse(self.cache.get_failed_urls())

    def test_queue_door_counts_a_banished_repo_settled_never_pending(self):
        # the queue classification's exact ladder: in_vault (a fresh
        # VaultIndex over the post-banishment vault) -> decommissioned
        # -> settled -> pending. The banished repo lands in SETTLED.
        url = 'https://github.com/owner/agent'
        p = _repo_note(self.vault, 'AI-Domain/agent.md', url)
        _mark(p)
        repo_banish.banish_marked_repos(
            self.cache, self.ns, self.vault, log=self.log)
        from gitcurator.gui.vault_index import VaultIndex
        vi = VaultIndex(self.vault)
        vi.rebuild(log_signal=None)
        self.assertFalse(vi.has_url(url))    # the note is gone
        self.assertFalse(self.cache.get_dead_url_set())
        self.assertIn(_links.normalize_url(url),
                      self.cache.get_settled_repo_set())

    def test_revive_then_rebanish_full_cycle(self):
        url = 'https://github.com/owner/cycle'
        p = _repo_note(self.vault, 'AI-Domain/cycle.md', url)
        _mark(p)
        repo_banish.banish_marked_repos(
            self.cache, self.ns, self.vault, log=self.log)
        self.assertTrue(self.cache.is_repo_settled(url))
        # the owner revives: the note comes back (his hand), the ♻️ row
        # opens the gates
        restored = _repo_note(self.vault, 'AI-Domain/cycle.md', url)
        _table(self.vault, [('♻️', url, 'revived')])
        repo_banish.revive_banished_repos(
            self.cache, self.ns, self.vault, log=self.log)
        self.assertFalse(self.cache.is_repo_settled(url))
        self.assertFalse(self.ns.is_dismissed(
            _note_state.VAULT_GITHUB, url))
        self.assertTrue(os.path.exists(restored))


# ---------------------------------------------------------------------------
# 6. The CacheDB helpers
# ---------------------------------------------------------------------------

class TestCacheHelpers(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='repobanish-cache-')
        self.db = CacheDB(os.path.join(self.tmp, 'cache.db'))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_processed_row_for_found_and_normalized(self):
        self.db.add_processed(
            7, 'https://github.com/owner/agent', 'owner', 'agent',
            'note.md', 'Agents')
        row = self.db.processed_row_for(
            'https://github.com/owner/agent/')   # trailing slash twin
        self.assertIsNotNone(row)
        self.assertEqual(row['repo_id'], 7)
        self.assertEqual(row['note_path'], 'note.md')
        self.assertEqual(row['owner'], 'owner')

    def test_processed_row_for_missing_is_none(self):
        self.assertIsNone(self.db.processed_row_for(
            'https://github.com/owner/never'))

    def test_forget_processed_url_true_then_false(self):
        self.db.add_processed(
            8, 'https://github.com/owner/gone', 'owner', 'gone',
            'n.md', 'Agents')
        self.assertTrue(self.db.forget_processed_url(
            'https://github.com/owner/gone'))
        self.assertIsNone(self.db.processed_row_for(
            'https://github.com/owner/gone'))
        self.assertFalse(self.db.forget_processed_url(
            'https://github.com/owner/gone'))

    def test_noop_settle_never_locks_the_other_connection(self):
        """v0.65.0 — the real-run proof's find: ``settle_repos`` used to
        commit only ``if added`` — an INSERT OR IGNORE that ignores (the
        idempotent no-op, the COMMON case: the banishment settling an
        already-settled repo) still opens a write transaction, and the
        skipped commit left cache.db write-locked for every OTHER
        connection (NoteStateDB) — "database is locked". The commit is
        unconditional now; this is the regression pin (two connections,
        the no-op settle, then the other connection must write)."""
        db2 = _note_state.NoteStateDB(os.path.join(self.tmp, 'cache.db'))
        try:
            self.db.settle_repos(['https://github.com/owner/agent'])
            # the no-op settle (nothing added) — the old code held the
            # write lock here
            self.assertEqual(
                self.db.settle_repos(['https://github.com/owner/agent']),
                0)
            # the OTHER connection writes cleanly — no "database is
            # locked"
            db2.dismiss(_note_state.VAULT_GITHUB,
                        'https://github.com/owner/agent', 'n.md')
            self.assertTrue(db2.is_dismissed(
                _note_state.VAULT_GITHUB,
                'https://github.com/owner/agent'))
        finally:
            db2.close()


# ---------------------------------------------------------------------------
# 7. Source contracts (the GUI wiring) + the note teaches the door
# ---------------------------------------------------------------------------

class TestSourceContracts(unittest.TestCase):

    def _read(self, *parts):
        with open(os.path.join(_REPO_ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_worker_runs_the_pass_before_the_settled_load(self):
        src = self._read("app", "gitcurator", "gui",
                         "processing_worker.py")
        self.assertIn("run_repo_banishment_pass", src)
        self.assertIn("THE REPOS BANISHMENT", src)
        self.assertIn("banish_confirm_factory", src)
        # the pass runs BEFORE the settled ledger is loaded (a freshly
        # banished repo joins it this same batch) — the order is the law
        self.assertLess(
            src.index("run_repo_banishment_pass"),
            src.index("get_settled_repo_set"))

    def test_the_module_keeps_the_hermetic_law(self):
        src = self._read("app", "gitcurator", "core", "repo_banish.py")
        # core never imports the network — the channel is injected
        self.assertNotIn("import requests", src)
        self.assertNotIn("cloudflare_sync", src)
        self.assertNotIn("make_telegram_confirm", src.split('"""')[2]
                         if src.count('"""') > 2 else src)

    def test_build_note_teaches_the_door(self):
        note = _note_builder.build_note(
            url='https://github.com/owner/agent', repo_name='agent',
            owner='owner', org_name='owner', stars=10, forks=2,
            commit_count=5, summary='A repo.', how_it_works='It works.',
            core_value='Value.', features=['a', 'b', 'c'],
            difference='Different.', category_key='Agents',
            confidence=80, cred_score=90.0, org_rep=3, tags=['cli'])
        self.assertIn('Not useful anymore? Tag this note', note)
        self.assertIn('never fetched or counted again', note)
        self.assertIn('Trash folder', note)


if __name__ == '__main__':
    unittest.main()

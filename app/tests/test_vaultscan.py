"""tests/test_vaultscan.py — v0.61.0 THE VAULT SCAN.

The owner's ask (session, verbatim): "HEADLINE — build the VAULT SCAN
mechanism (v0.61.0). New CTA row: [Fetch] [Scan] [Test Connection].
The Scan run: LLM scans the vault (folder walk + note contents → LLM
analysis); Detects notes I manually tagged 'auto-delete' — REUSE the
v0.60.1 grammar exactly (frontmatter list + body inline tags, both
auto_delete/auto-delete spellings, 🗑️, BANISH_WORDs) — do not invent a
second grammar; Suggests folder/subfolder creation for orphaned /
not-categorized / too-broad-category websites, and which notes should
move where; Confirms with me on Telegram BEFORE deleting or moving
anything — the v0.60.0 banish-gate round-trip is the template."

Covered here (zero network — a fake llm_call and a scripted opener,
the house pattern):

* the inventory — the folder walk, the per-note digest, the protected
  folders seen but never candidates, hand notes counted never moved;
* the move candidates — orphaned (vault root), uncategorized, and the
  too-broad threshold;
* THE GRAMMAR REUSE — the scan's deletions ARE
  scan_pending_banishments' items, both doors, the same markers;
* the LLM pass — the validated proposal (legal destinations only,
  listed candidates only), a broken answer is an empty plan, never a
  crash;
* the apply pass — folders created, notes MOVED byte-identical, state
  rows re-pointed, nothing ever overwritten, dry-run rehearses;
* the channel — make_scan_confirm's round trip over a scripted opener
  (propose → poll → report), the UA law, the timeout defer.
"""

import json
import os
import shutil
import tempfile
import types
import unittest
import urllib.request
from unittest import mock

from gitcurator.core import dryrun
from gitcurator.core import vault_scan as vs
from gitcurator.core import website_pipeline as wp


# -- the fakes -------------------------------------------------------------

class _FakeLLM:
    """Returns a canned filing plan; captures the prompt it was fed."""

    def __init__(self, plan=None, raw=None):
        self.plan = plan or {}
        self.raw = raw
        self.calls = []

    def __call__(self, messages, task=None):
        self.calls.append((messages, task))
        if self.raw is not None:
            return self.raw
        return json.dumps(self.plan)


def _note(path, url, managed='gitcurator', tags=None, body='',
          category='', subcategory=''):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tag_line = 'tags: [' + ', '.join(tags or []) + ']'
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"""---
source: "{url or ''}"
{tag_line}
category: "{category}"
subcategory: "{subcategory}"
fetch_status: "full"
managed_by: "{managed}"
---

# A Note

{body}
""")
    return path


class _ScanCase(unittest.TestCase):
    """Shared plumbing: temp vault + state db."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='vaultscan-')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(self.vault, exist_ok=True)
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))
        self.logs = []

    def tearDown(self):
        dryrun.disable()
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def log(self, m, l='info'):
        self.logs.append((l, m))

    def all_logs(self):
        return '\n'.join(m for (_, m) in self.logs)


class TestInventory(_ScanCase):

    def test_the_walk_counts_folders_and_notes(self):
        _note(os.path.join(self.vault, 'AI-Domain', 'Agents', 'A.md'),
              'https://a.example/x', category='Agents')
        _note(os.path.join(self.vault, 'AI-Domain', 'Agents', 'B.md'),
              'https://b.example/y', category='Agents')
        _note(os.path.join(self.vault, 'Tools', 'Scraping', 'C.md'),
              'https://c.example/z')
        inv = vs.scan_vault_inventory(self.vault)
        self.assertEqual(inv['total_notes'], 3)
        by_rel = {f['rel']: f['note_count']
                  for f in inv['folders']}
        self.assertEqual(by_rel['AI-Domain/Agents'], 2)
        self.assertEqual(by_rel['Tools/Scraping'], 1)
        a = [n for n in inv['notes'] if n['file'] == 'A.md'][0]
        self.assertEqual(a['domain'], 'a.example')
        self.assertEqual(a['category'], 'Agents')
        self.assertTrue(a['app_owned'])

    def test_protected_folders_are_seen_never_candidates(self):
        _note(os.path.join(self.vault, '_review', 'placeholder.md'),
              'https://r.example/p')
        _note(os.path.join(self.vault, '_moc', 'index.md'),
              'https://r.example/i')
        inv = vs.scan_vault_inventory(self.vault)
        self.assertEqual(inv['total_notes'], 2)
        for n in inv['notes']:
            self.assertTrue(n['protected'])
        self.assertEqual(vs._move_candidates(inv, 40), [])

    def test_hand_notes_are_counted_never_moved(self):
        _note(os.path.join(self.vault, 'Uncategorized', 'mine.md'),
              '', managed='a-human')
        inv = vs.scan_vault_inventory(self.vault)
        self.assertEqual(inv['hand_notes'], 1)
        self.assertEqual(inv['uncategorized_notes'], 1)
        self.assertEqual(vs._move_candidates(inv, 40), [])

    def test_dot_folders_and_inbox_are_invisible(self):
        _note(os.path.join(self.vault, '.obsidian', 'x.md'),
              'https://d.example/x')
        _note(os.path.join(self.vault, '_inbox', 'y.md'),
              'https://d.example/y')
        inv = vs.scan_vault_inventory(self.vault)
        self.assertEqual(inv['total_notes'], 0)


class TestMoveCandidates(_ScanCase):

    def test_root_and_uncategorized_and_broad(self):
        _note(os.path.join(self.vault, 'orphan.md'),
              'https://o.example/1')                       # the root
        _note(os.path.join(self.vault, 'Uncategorized',
                           'parked.md'), 'https://u.example/2')
        for i in range(3):
            _note(os.path.join(self.vault, 'AI-Domain', 'LLM-Tools',
                               f'n{i}.md'), f'https://t.example/{i}')
        _note(os.path.join(self.vault, 'Tools', 'Scraping',
                           'small.md'), 'https://s.example/3')
        inv = vs.scan_vault_inventory(self.vault)
        cands = {n['file'] for n in vs._move_candidates(inv, 2)}
        self.assertEqual(cands,
                         {'orphan.md', 'parked.md', 'n0.md', 'n1.md',
                          'n2.md'})
        # threshold 3 (nothing broad): only the orphan + parked stay
        cands3 = {n['file'] for n in vs._move_candidates(inv, 3)}
        self.assertEqual(cands3, {'orphan.md', 'parked.md'})


class TestTheGrammarReuse(_ScanCase):
    """THE LAW: the scan's deletions ARE scan_pending_banishments —
    one grammar, both doors, the exact v0.60.1 markers."""

    def test_deletions_are_the_banish_gate_items(self):
        # every door: frontmatter tag, body inline tag, emoji tag,
        # boolean key, table gesture — plus a negative control:
        _note(os.path.join(self.vault, 'Design', 'fm.md'),
              'https://g.example/fm', tags=['auto_delete'])
        _note(os.path.join(self.vault, 'Design', 'body.md'),
              'https://g.example/body', body='not needed #auto-delete')
        _note(os.path.join(self.vault, 'Design', 'emoji.md'),
              'https://g.example/emoji', tags=['🗑️'])
        _note(os.path.join(self.vault, 'Design', 'key.md'),
              'https://g.example/key', category='x')
        p = os.path.join(self.vault, 'Design', 'key.md')
        with open(p, 'r', encoding='utf-8') as f:
            t = f.read()
        t = t.replace('category: "x"', 'category: "x"\nblacklist: true')
        with open(p, 'w', encoding='utf-8') as f:
            f.write(t)
        _note(os.path.join(self.vault, 'Design', 'negative.md'),
              'https://g.example/neg', body='#deleted-files not a mark')
        table = os.path.join(self.vault, '_review', 'DECOMMISSIONED.md')
        os.makedirs(os.path.dirname(table), exist_ok=True)
        with open(table, 'w', encoding='utf-8') as f:
            f.write('| # | Date | URL | Domain | Source | Status | Notes |\n'
                    '|---|------|-----|--------|--------|--------|-------|\n'
                    '| 🗑️ | 2026-10-09 | https://g.example/table | g.example'
                    ' | test | 🗑️ banished | |')
        gate = wp.scan_pending_banishments(self.vault)
        plan = vs.build_scan_plan(self.vault, llm_call=None, log=self.log)
        self.assertEqual(
            [d['canonical'] for d in plan['deletions']],
            [i['canonical'] for i in gate['items']])
        self.assertEqual(len(plan['deletions']), 5)   # the negative
        # control never rides (fm, body, emoji, key, table)
        markers = {d['marker'] for d in plan['deletions']}
        self.assertIn('auto_delete', markers)
        self.assertIn('#auto-delete', markers)
        self.assertIn('🗑️', markers)
        self.assertIn('blacklist: true', markers)


class TestTheLLMPass(_ScanCase):

    def _inventory_with_candidates(self):
        _note(os.path.join(self.vault, 'Uncategorized', 'Agent One.md'),
              'https://a.example/one', tags=['agents'])
        _note(os.path.join(self.vault, 'Uncategorized', 'Agent Two.md'),
              'https://a.example/two', tags=['agents'])
        _note(os.path.join(self.vault, 'AI-Domain', 'Agents',
                           'Existing.md'), 'https://a.example/existing')
        return vs.scan_vault_inventory(self.vault)

    def test_a_valid_proposal_lands(self):
        inv = self._inventory_with_candidates()
        llm = _FakeLLM(plan={
            'new_folders': ['AI-Domain/Agents/Agent-Frameworks'],
            'moves': [
                {'note': 'Agent One.md',
                 'to': 'AI-Domain/Agents/Agent-Frameworks',
                 'reason': 'an agent framework'},
                {'note': 'Agent Two.md',
                 'to': 'AI-Domain/Agents',
                 'reason': 'agent tooling'}],
            'summary': 'Two agent notes filed.'})
        plan = vs.build_scan_plan(self.vault, llm_call=llm, log=self.log)
        self.assertEqual(len(plan['moves']), 2)
        self.assertEqual(plan['new_folders'],
                         ['AI-Domain/Agents/Agent-Frameworks'])
        self.assertEqual(plan['summary'], 'Two agent notes filed.')
        # the LLM saw the tree + the candidates, tagged 'vaultscan':
        self.assertEqual(len(llm.calls), 1)
        msgs, task = llm.calls[0]
        self.assertEqual(task, 'vaultscan')
        self.assertIn('Uncategorized', msgs[1]['content'])
        self.assertIn('Agent One', msgs[1]['content'])
        self.assertIn('notes', msgs[1]['content'])  # the tree lines

    def test_unlisted_notes_and_illegal_destinations_drop(self):
        self._inventory_with_candidates()
        llm = _FakeLLM(plan={
            'new_folders': ['../escape', 'AI-Domain/Agents/New-Sub',
                             'Tools/No-Parent'],
            'moves': [
                {'note': 'Existing.md',
                 'to': 'AI-Domain/Agents/New-Sub', 'reason': 'no'},
                {'note': 'Not Listed.md',
                 'to': 'AI-Domain/Agents', 'reason': 'no'},
                {'note': 'Agent One.md',
                 'to': '_review', 'reason': 'never'},
                {'note': 'Agent One.md',
                 'to': 'AI-Domain/Agents/New-Sub', 'reason': 'yes'},
                {'note': 'Agent Two.md',
                 'to': 'AI-Domain/Agents', 'reason': 'yes'}],
            'summary': ''})
        plan = vs.build_scan_plan(self.vault, llm_call=llm, log=self.log)
        # only the two listed candidates with legal destinations move;
        # Existing.md (not a candidate) and Not Listed.md never ride:
        self.assertEqual(
            sorted(m['note'] for m in plan['moves']),
            ['Agent One.md', 'Agent Two.md'])
        # new folders: the parented one stays; ../escape fails the
        # sanitizer; Tools/No-Parent has no parent in the tree:
        self.assertEqual(plan['new_folders'],
                         ['AI-Domain/Agents/New-Sub'])
        self.assertIn('not a legal destination', self.all_logs())
        self.assertIn('no parent in the tree', self.all_logs())

    def test_a_broken_answer_is_an_empty_plan_never_a_crash(self):
        self._inventory_with_candidates()
        plan = vs.build_scan_plan(
            self.vault, llm_call=_FakeLLM(raw='total garbage {'),
            log=self.log)
        self.assertEqual(plan['moves'], [])
        self.assertEqual(plan['new_folders'], [])
        self.assertIn('not JSON I could read', self.all_logs())
        # an LLM that RAISES is survived too:
        def boom(messages, task=None):
            raise RuntimeError('the model is down')
        plan2 = vs.build_scan_plan(self.vault, llm_call=boom,
                                   log=self.log)
        self.assertEqual(plan2['moves'], [])
        self.assertIn('LLM call failed', self.all_logs())

    def test_no_candidates_mean_no_llm_call(self):
        _note(os.path.join(self.vault, 'Design', 'Fine.md'),
              'https://f.example/ok')
        llm = _FakeLLM(plan={'moves': [{'note': 'Fine.md', 'to': 'X'}]})
        plan = vs.build_scan_plan(self.vault, llm_call=llm, log=self.log)
        self.assertEqual(llm.calls, [])     # never asked
        self.assertEqual(plan['moves'], [])


class TestTheApplyPass(_ScanCase):

    def _plan(self):
        _note(os.path.join(self.vault, 'Uncategorized', 'Mover.md'),
              'https://m.example/one', category='Uncategorized')
        return {'new_folders': ['AI-Domain/Agents'],
                'moves': [{
                    'note': 'Mover.md',
                    'path': os.path.join(self.vault, 'Uncategorized',
                                         'Mover.md'),
                    'from': 'Uncategorized', 'to': 'AI-Domain/Agents',
                    'reason': 'agents'}],
                'deletions': []}

    def test_moves_are_byte_identical_and_state_repointed(self):
        plan = self._plan()          # creates the note (and its dirs)
        src = os.path.join(self.vault, 'Uncategorized', 'Mover.md')
        with open(src, 'w', encoding='utf-8') as f:
            f.write('---\nsource: "https://m.example/one"\n---\nBYTES ⚙️\n')
        before = open(src, 'rb').read()
        rep = vs.apply_scan_plan(plan, self.vault, self.db, log=self.log)
        self.assertEqual(rep['folders_created'], 1)
        self.assertEqual(rep['notes_moved'], 1)
        dst = os.path.join(self.vault, 'AI-Domain', 'Agents', 'Mover.md')
        self.assertTrue(os.path.isfile(dst))
        self.assertFalse(os.path.exists(src))
        with open(dst, 'rb') as f:
            self.assertEqual(f.read(), before)   # byte-identical
        row = self.db.processed_row(
            wp.normalize_website_url('https://m.example/one'))
        self.assertEqual(row['note_path'], dst)
        self.assertEqual(row['category'], 'AI-Domain')
        self.assertIn('moved → AI-Domain/Agents', self.all_logs())

    def test_nothing_is_ever_overwritten(self):
        plan = self._plan()
        dst = os.path.join(self.vault, 'AI-Domain', 'Agents', 'Mover.md')
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'w', encoding='utf-8') as f:
            f.write('THE OWNER\'S OWN NOTE')
        rep = vs.apply_scan_plan(plan, self.vault, self.db, log=self.log)
        self.assertEqual(rep['notes_moved'], 0)
        self.assertEqual(rep['failed_moves'], 1)
        with open(dst, encoding='utf-8') as f:
            self.assertEqual(f.read(), 'THE OWNER\'S OWN NOTE')
        self.assertTrue(os.path.exists(
            os.path.join(self.vault, 'Uncategorized', 'Mover.md')))
        self.assertIn('nothing is overwritten', self.all_logs())

    def test_dry_run_rehearses_moves(self):
        plan = self._plan()
        dryrun.enable()
        try:
            rep = vs.apply_scan_plan(plan, self.vault, self.db,
                                     log=self.log)
        finally:
            dryrun.disable()
        self.assertEqual(rep['notes_moved'], 1)   # the would-be story
        self.assertEqual(rep['folders_created'], 1)
        self.assertTrue(os.path.exists(
            os.path.join(self.vault, 'Uncategorized', 'Mover.md')))
        self.assertFalse(os.path.exists(
            os.path.join(self.vault, 'AI-Domain', 'Agents', 'Mover.md')))
        self.assertIn('would move', self.all_logs())

    def test_the_deletions_ride_the_banish_machinery(self):
        _note(os.path.join(self.vault, 'Design', 'Marked.md'),
              'https://d.example/marked', tags=['auto_delete'])
        plan = {'new_folders': [], 'moves': [], 'deletions': []}
        rep = vs.apply_scan_plan(plan, self.vault, self.db, log=self.log)
        self.assertEqual(rep['banished'], 1)
        self.assertFalse(os.path.exists(
            os.path.join(self.vault, 'Design', 'Marked.md')))
        self.assertTrue(self.db.is_dismissed(
            wp.normalize_website_url('https://d.example/marked')))


class _ScriptedResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _ScriptedOpener:
    def __init__(self, responses):
        self.requests = []
        self._responses = list(responses)

    def open(self, req, timeout=None):
        self.requests.append(req)
        if not self._responses:
            raise AssertionError('unexpected request')
        status, body = self._responses.pop(0)
        return _ScriptedResponse(status, body)


def _ua(req):
    return (req.headers or {}).get('User-agent') or ''


class TestTheScanChannel(unittest.TestCase):
    """make_scan_confirm — the round trip over a scripted opener (the
    test_channelua pattern)."""

    _PAIRED = {
        'cloudflare_worker_url': 'https://bot.example.workers.dev',
        'cloudflare_install_id': '11111111-2222-3333-4444-555555555555',
        'cloudflare_shared_secret': 'a' * 64,
        'cloudflare_enabled': True,
        'scan_confirm_timeout_s': 30,
    }

    def _confirm(self, responses, plan, apply=None):
        from gitcurator.integrations import scan_confirm as sc
        from gitcurator.cloud import cloudflare_sync as cs
        opener = _ScriptedOpener(responses)
        with mock.patch.object(cs.urllib.request, 'build_opener',
                               return_value=opener), \
                mock.patch.object(sc, 'POLL_INTERVAL_S', 0):
            channel = sc.make_scan_confirm(self._PAIRED)
            self.assertIsNotNone(channel)
            res = channel(plan)
            if apply and res.get('report'):
                res['report'](*apply)   # INSIDE the patch — no network
            return opener, res

    def test_unpaired_answers_none(self):
        from gitcurator.integrations.scan_confirm import make_scan_confirm
        self.assertIsNone(make_scan_confirm({
            'cloudflare_worker_url': 'https://bot.example.workers.dev'}))

    def test_the_confirmed_round_trip(self):
        plan = {
            'deletions': [{'url': 'https://x.example/a', 'title': 'A',
                           'marker': 'auto_delete', 'door': 'note tag'}],
            'moves': [{'note': 'B.md', 'from': 'Uncategorized',
                       'to': 'AI-Domain/Agents', 'reason': 'r'}],
            'new_folders': ['AI-Domain/Agents'], 'summary': 's'}
        opener, res = self._confirm([
            (200, json.dumps({'success': True, 'id': 'scan-1',
                              'message_id': 9}).encode()),
            (200, json.dumps({'success': True, 'status': 'confirmed',
                              'deletions': 1, 'moves': 1,
                              'new_folders': 1}).encode()),
            (200, json.dumps({'success': True, 'status': 'done'}).encode()),
        ], plan, apply=(1, 1, 1))
        self.assertEqual(res['verdict'], 'confirmed')
        self.assertTrue(callable(res['report']))
        self.assertEqual(len(opener.requests), 3)
        # the propose carried the whole plan:
        body = json.loads(opener.requests[0].data.decode('utf-8'))
        self.assertEqual(body['deletions'], 1)
        self.assertEqual(body['moves'], 1)
        self.assertEqual(body['new_folders'], 1)
        self.assertEqual(len(body['items']), 2)   # delete + move
        self.assertEqual(body['items'][0]['kind'], 'delete')
        self.assertEqual(body['items'][1]['kind'], 'move')
        # the 1010 law rides every leg:
        from gitcurator.cloud.cloudflare_sync import HTTP_USER_AGENT
        for req in opener.requests:
            self.assertEqual(_ua(req), HTTP_USER_AGENT)

    def test_the_timeout_defer(self):
        plan = {'deletions': [{'url': 'https://x.example/a',
                               'title': 'A', 'marker': 'm',
                               'door': 'd'}],
                'moves': [], 'new_folders': [], 'summary': ''}
        opener, res = self._confirm([
            (200, json.dumps({'success': True, 'id': 'scan-2',
                              'message_id': 10}).encode()),
            (200, json.dumps({'success': True, 'status': 'timeout'}
                             ).encode()),
            (200, json.dumps({'success': True, 'status': 'timeout'}
                             ).encode()),
        ], plan)
        self.assertEqual(res['verdict'], 'timeout')
        self.assertIsNone(res['report'])

    def test_a_failed_propose_defers_with_a_reason(self):
        plan = {'deletions': [{'url': 'https://x.example/a',
                               'title': 'A', 'marker': 'm',
                               'door': 'd'}],
                'moves': [], 'new_folders': [], 'summary': ''}
        logs = []

        def log(m, l='info'):
            logs.append(m)

        from gitcurator.integrations import scan_confirm as sc
        from gitcurator.cloud import cloudflare_sync as cs
        opener = _ScriptedOpener([
            (502, json.dumps({'error': 'Telegram send failed'}).encode())])
        with mock.patch.object(cs.urllib.request, 'build_opener',
                               return_value=opener):
            channel = sc.make_scan_confirm(self._PAIRED, log=log)
            res = channel(plan, ask_log=log)
        self.assertEqual(res['verdict'], 'defer')
        self.assertIn('could not reach Telegram', '\n'.join(logs))


class TestReleaseBookkeeping(unittest.TestCase):
    """The house source-contract tests."""

    def setUp(self):
        self.root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), 'r',
                  encoding='utf-8') as f:
            return f.read()

    def test_version_is_0611(self):
        # v0.61.1 — the Scan-CTA crash fix (see TestScanCtaWiring below).
        self.assertEqual(self._read('VERSION').strip(), '0.62.0')

    def test_the_prompt_exists_and_names_the_law(self):
        text = self._read('app', 'prompts', 's01_vaultscan.txt')
        self.assertIn('new_folders', text)
        self.assertIn('moves', text)
        self.assertIn('NEVER propose deleting', text)

    def test_ci_and_agents_know_the_module(self):
        ci = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('tests.test_vaultscan', ci)
        agents = self._read('AGENTS.md')
        self.assertIn('tests.test_vaultscan', agents)

    def test_changelog_has_the_fix(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.61.1]', text)
        self.assertIn('log_signal', text)
        # the history stays: the 0.61.0 beat is still told.
        self.assertIn('## [0.61.0]', text)
        self.assertIn('vault scan', text.lower())


# ---------------------------------------------------------------------------
# v0.61.1 — the Scan CTA's worker wiring (the owner's crash, 2026-10-10)
# ---------------------------------------------------------------------------

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    _QAPP = QApplication.instance() or QApplication([])
    from gitcurator.gui.main_window.vault_scan_ui import VaultScanUiMixin
    from gitcurator.gui.processing_worker import TestWorker
    _HAS_QT = True
except Exception:  # pragma: no cover — CI installs PyQt6
    _HAS_QT = False


@unittest.skipUnless(_HAS_QT, "PyQt6 unavailable")
class TestScanCtaWiring(unittest.TestCase):
    """v0.61.1 — Fix: the Scan button crashed on EVERY click.

    The owner's run (v0.61.0, 2026-10-10): "💥 Worker 'vault_scan'
    crashed: TypeError: _vault_scan_job() missing 1 required positional
    argument: 'log_signal'" — the mixin built TestWorker(_vault_scan_job,
    'vault_scan', snapshot) and run() calls fn(*args): the signal was
    never fed in. The fix re-binds worker._fn to a closure passing
    worker.log_message — the exact pattern Test Connection's battery
    uses. These tests pin that law so it can never regress silently
    again (the v0.61.0 suite was green while the button was dead)."""

    def _window(self):
        win = VaultScanUiMixin()
        win.logs = []
        win.worker = None
        win.config = {'website_vault_path': '/tmp/vault'}
        win.website_vault_input = types.SimpleNamespace(
            text=lambda: '')
        win._btn_calls = []

        def _set_enabled(b):
            win._btn_calls.append(('enabled', b))

        def _set_text(t):
            win._btn_calls.append(('text', t))
        win.scan_btn = types.SimpleNamespace(
            setEnabled=_set_enabled, setText=_set_text)

        def _log(msg, level='info'):
            win.logs.append((level, msg))
        win.log_message = _log
        return win

    def test_the_bound_fn_takes_only_the_snapshot_and_feeds_the_signal(self):
        """THE regression: fn(snapshot) must run without TypeError and
        hand the job (config, worker.log_message) — a live signal whose
        emissions land in the window's log."""
        from gitcurator.gui.main_window import vault_scan_ui as _vsui
        win = self._window()
        seen = {}

        def _fake_job(cfg, log_signal, confirm_gui=None):
            # v0.62.0 — the job now takes the GUI ask-gate too (the
            # wiring feeds worker.request_scan_confirm in)
            seen['cfg'] = cfg
            seen['signal'] = log_signal
            seen['confirm_gui'] = confirm_gui
            return {'success': True, 'verdict': 'clean'}

        with mock.patch.object(_vsui, '_vault_scan_job', _fake_job), \
                mock.patch.object(TestWorker, 'start',
                                  lambda self: None):  # wiring, not threads
            win._start_vault_scan()
            w = win._scan_worker
            self.assertIsInstance(w, TestWorker)
            # THE LAW: the re-bound fn is callable with the snapshot
            # alone — the v0.61.0 bug raised TypeError right here.
            result = w._fn(dict(win.config))
        self.assertEqual(result.get('verdict'), 'clean')
        self.assertEqual(seen['cfg'].get('website_vault_path'),
                         '/tmp/vault')
        # v0.62.0 — THE GUI CONFIRM DOOR'S wiring law: the ask-gate
        # rides in (the worker's request_scan_confirm — the login-code
        # pattern's twin), never None at the desk.
        self.assertIsNotNone(seen.get('confirm_gui'))
        self.assertIn('request_scan_confirm',
                      repr(seen.get('confirm_gui')))
        # PyQt re-binds the signal on every attribute access, so object
        # identity can't be asserted — the LIVE-EMISSION proof below is
        # the real law: the fed signal reaches the window's log.
        self.assertIn('log_message', repr(seen['signal']))
        seen['signal'].emit("the pipe works", "info")
        self.assertIn(('info', 'the pipe works'), win.logs)

    def test_the_start_story_and_the_re_arm(self):
        """The CTA speaks its opening line, disables the button, and the
        finished routing re-arms it + speaks the verdict."""
        from gitcurator.gui.main_window import vault_scan_ui as _vsui
        win = self._window()
        with mock.patch.object(_vsui, '_vault_scan_job',
                               lambda cfg, sig, confirm_gui=None: {
                                   'success': True, 'verdict': 'clean'}), \
                mock.patch.object(TestWorker, 'start',
                                  lambda self: None):
            win._start_vault_scan()
        self.assertIn('enabled', [c[0] for c in win._btn_calls])
        self.assertIn(('text', 'Scanning…'), win._btn_calls)
        self.assertTrue(any('Vault scan started' in m
                            for _l, m in win.logs))
        win._vault_scan_finished('vault_scan', {'verdict': 'clean'})
        self.assertIn(('text', 'Scan'), win._btn_calls)
        self.assertTrue(any('library is clean' in m
                            for _l, m in win.logs))

    def test_the_v0610_shape_is_dead_forever(self):
        """The old wiring — fn IS the raw job, args=(snapshot,) — is the
        bug. Pin that _fn is a re-bound wrapper, never the job itself."""
        from gitcurator.gui.main_window import vault_scan_ui as _vsui
        from gitcurator.gui import worker_jobs as _wjobs
        win = self._window()
        with mock.patch.object(TestWorker, 'start', lambda self: None):
            win._start_vault_scan()
        w = win._scan_worker
        self.assertIsNot(w._fn, _wjobs._vault_scan_job)
        # and calling the wrapper with the old single arg still works:
        r = w._fn({'website_vault_path': '/tmp/vault'})
        self.assertIsInstance(r, dict)


if __name__ == '__main__':
    unittest.main()

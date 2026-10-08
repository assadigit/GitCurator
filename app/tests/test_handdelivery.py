"""tests/test_handdelivery.py — v0.48.0, the fourth door: the owner's
own Chrome.

The owner's report (session): "for some links, the fetcher got 403
error, but when I enter them manually on my browser they work
flawlessly… What if we add another layer (as one of the final
procedures) of fetching, which app permits me to open in a real
instance of chrome."

Covered here (unit law: temp vaults, patched seams, zero sockets, no
browser ever launches):

* the wall predicate — exactly the refusal-family / bot-defense /
  TLS-handshake / challenge / third-door report classes earn the
  fourth door; resource truths (404, paywall, DNS, redirect) never
  ask the owner's hand;
* the suggested filename — deterministic per URL, collision-proof on
  one domain, sane for odd URLs;
* the queue — enqueue merges (no duplicates), rides the wall reason,
  rewrites the README with every suggested name, resets a consumed
  link on re-enqueue, tolerates a corrupt queue.json, and dry-run
  records without writing;
* the real Chrome — the finder's ladder (an exe path wins, the system
  browser is the honest fallback, nothing found is a named miss),
  loopback NEVER opens a browser, the launch is detached and never
  raises;
* the consume side — a delivered page is collected, consumed (queue
  stamped, FILE kept — it is the record), and becomes a REAL full
  FetchResult with the story in the reason; an empty or unreadable
  page keeps waiting; orphans are ignored;
* the master-table gesture — 🖐 hand queues (never retires: no
  dismissal, the retry row stays), the row is stamped, a second pass
  never re-stamps (idempotent), death and reviewed still outrank it,
  and the header grammar names the door;
* the pipeline — a delivered page answers BEFORE any machine door
  (the injected fetcher is never asked), the note is written, the
  retry row resolves; run() pulls delivered pages into the batch
  uninvited; a walled failure gains the fourth-door hint in its
  reason/note; "web_hand_delivery": false opts the whole door out.

No PyQt import at module level (the libEGL-less sandbox rule).
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from gitcurator.core import dryrun
from gitcurator.core import hand_delivery as hd
from gitcurator.core import website_pipeline as wp
from gitcurator.core import web_fetch as wf


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

_WALL_URL = 'https://walled.example.net/article'
_WALL_REASON = ('HTTP 403 — bot defense (server: cloudflare; '
                'challenge) | chrome-impersonated: HTTP 403')


class _FakeLimiter:
    def wait(self, domain):
        pass

    def penalize(self, domain, seconds):
        pass


class _FakeFetch:
    """Canned result, **kwargs-tolerant, counts its calls."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        return self.result


def _ok(url, body=b'<html><head><title>The Walled Page</title></head>'
        b'<body>the content the browser sees</body></html>'):
    return wf.FetchResult(url, final_url=url, status='full',
                          http_status=200, content_type='text/html',
                          charset='utf-8', body=body,
                          text=body.decode('utf-8'))


def _res(reason, http_status=None, category='', url=_WALL_URL):
    return wf.FetchResult(url, status='failed', reason=reason,
                          http_status=http_status, category=category)


class _FakeLLM:
    """Prompt-aware fake (the phase-2 pattern): the classify prompt
    gets a REAL taxonomy category, the subcategory prompt its answer,
    the analysis prompt the site card — a delivered page must be able
    to reach 'processed', not die in classification."""

    def __call__(self, messages, task=None):
        text = messages[0]['content']
        if 'filing a website into a personal library' in text:
            return json.dumps({'category': 'Design',
                               'confidence': 'high',
                               'reason': 'testing'})
        if 'was filed under' in text:
            return json.dumps({'subcategory': 'Assets & Resources',
                               'confidence': 'medium'})
        return json.dumps({
            'name': 'Walled Site', 'one_line': 'A page behind a wall.',
            'core_offerings': ['One'],
            'best_used_for': 'Use when you need to beat a bot wall.',
            'pricing': 'free', 'login_required': 'no',
            'similar_tools': [], 'tags': ['design'],
            'confidence': 'high'})


class _HandCase(unittest.TestCase):
    """Temp vault + state DB, cleaned up."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='hand-')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(self.vault, exist_ok=True)
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))

    def tearDown(self):
        dryrun.disable()
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def queue_file(self, url, wall=_WALL_REASON, consumed=False):
        """Drop a queue.json + the delivered page (or leave the file
        out — the caller writes it separately)."""
        hd.enqueue_hand_delivery(self.vault, [url], walls={url: wall},
                                 log=lambda *a, **k: None)

    def deliver(self, url, body=b'<html><head><title>Hand-saved</title>'
                b'</head><body>saved by the owner</body></html>'):
        sug = hd.suggested_filename(url)
        path = os.path.join(hd.hand_delivery_dir(self.vault), sug)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(body)
        return path


# ---------------------------------------------------------------------------
# 1. the wall predicate
# ---------------------------------------------------------------------------

class TestWallPredicate(unittest.TestCase):

    def test_refusal_family_earns_the_door(self):
        for reason in ('HTTP 403', 'proxy: HTTP 405 | direct: HTTP 405',
                       'HTTP 429 — bot defense', 'HTTP 451'):
            self.assertTrue(hd.is_walled_reason(reason), reason)

    def test_bot_defense_and_challenge_earn_the_door(self):
        self.assertTrue(hd.is_walled_reason(
            'HTTP 403 — bot defense (server: cloudflare)'))
        self.assertTrue(hd.is_walled_reason(
            'the challenge needs a live browser'))

    def test_tls_handshake_and_third_door_lines_earn_the_door(self):
        self.assertTrue(hd.is_walled_reason(
            'proxy: connection: [SSL: SSLV3_ALERT_HANDSHAKE_FAILURE]'))
        self.assertTrue(hd.is_walled_reason(
            'chrome-impersonated: HTTP 403'))
        self.assertTrue(hd.is_walled_reason(
            'a third door exists: pip install curl_cffi'))

    def test_resource_truths_never_ask_the_owner(self):
        for reason in ('HTTP 404 — the page is gone', 'HTTP 402 — paywalled',
                       'HTTP 523', 'timeout', 'connection: reset by peer',
                       'not an http(s) URL'):
            self.assertFalse(hd.is_walled_reason(reason), reason)

    def test_empty_is_not_a_wall(self):
        self.assertFalse(hd.is_walled_reason(''))
        self.assertFalse(hd.is_walled_reason(None))

    def test_walled_retry_rows_filters_the_queue(self):
        rows = [{'url': _WALL_URL, 'last_error': _WALL_REASON,
                 'attempts': 2},
                {'url': 'https://dead.example.org/x',
                 'last_error': 'HTTP 404 — gone', 'attempts': 1},
                {'url': 'https://ok.example.org/y',
                 'last_error': 'timeout', 'attempts': 1}]

        class _State:
            def all_retry_rows(self):
                return rows

        out = hd.walled_retry_rows(_State())
        self.assertEqual([r['url'] for r in out], [_WALL_URL])
        self.assertEqual(out[0]['wall'], _WALL_REASON)
        self.assertEqual(out[0]['attempts'], 2)

    def test_state_failure_means_an_empty_picker(self):
        class _Boom:
            def all_retry_rows(self):
                raise RuntimeError('db locked')

        self.assertEqual(hd.walled_retry_rows(_Boom()), [])


# ---------------------------------------------------------------------------
# 2. the suggested filename
# ---------------------------------------------------------------------------

class TestSuggestedFilename(unittest.TestCase):

    def test_deterministic_for_one_url(self):
        a = hd.suggested_filename(_WALL_URL)
        b = hd.suggested_filename(_WALL_URL)
        self.assertEqual(a, b)
        self.assertTrue(a.startswith('hand-'))
        self.assertTrue(a.endswith('.html'))

    def test_two_pages_on_one_domain_never_collide(self):
        a = hd.suggested_filename('https://site.example.net/a')
        b = hd.suggested_filename('https://site.example.net/b')
        self.assertNotEqual(a, b)
        self.assertNotEqual(hd.suggested_filename(''),
                            hd.suggested_filename('x'))


# ---------------------------------------------------------------------------
# 3. the queue (write side)
# ---------------------------------------------------------------------------

class TestEnqueue(_HandCase):

    def test_enqueue_writes_queue_and_readme(self):
        report = hd.enqueue_hand_delivery(
            self.vault, [_WALL_URL], walls={_WALL_URL: _WALL_REASON},
            log=lambda *a, **k: None)
        self.assertEqual(report['added'], 1)
        self.assertEqual(report['queued_total'], 1)
        with open(hd.queue_path(self.vault), encoding='utf-8') as f:
            queue = json.load(f)
        self.assertIn(_WALL_URL, queue['links'])
        self.assertEqual(queue['links'][_WALL_URL]['wall'], _WALL_REASON)
        self.assertEqual(queue['links'][_WALL_URL]['suggested'],
                         hd.suggested_filename(_WALL_URL))
        with open(hd.readme_path(self.vault), encoding='utf-8') as f:
            readme = f.read()
        self.assertIn(_WALL_URL, readme)
        self.assertIn(hd.suggested_filename(_WALL_URL), readme)
        self.assertIn(_WALL_REASON[:40], readme)

    def test_reenqueue_is_a_noop_merge(self):
        hd.enqueue_hand_delivery(self.vault, [_WALL_URL],
                                 log=lambda *a, **k: None)
        report = hd.enqueue_hand_delivery(self.vault, [_WALL_URL],
                                          log=lambda *a, **k: None)
        self.assertEqual(report['added'], 0)
        self.assertEqual(report['queued_total'], 1)

    def test_reenqueue_of_a_consumed_link_resets_it(self):
        hd.enqueue_hand_delivery(self.vault, [_WALL_URL],
                                 log=lambda *a, **k: None)
        with open(hd.queue_path(self.vault), encoding='utf-8') as f:
            queue = json.load(f)
        queue['links'][_WALL_URL]['consumed'] = '2026-10-08 10:00'
        with open(hd.queue_path(self.vault), 'w', encoding='utf-8') as f:
            json.dump(queue, f)
        report = hd.enqueue_hand_delivery(self.vault, [_WALL_URL],
                                          log=lambda *a, **k: None)
        self.assertEqual(report['added'], 1)
        with open(hd.queue_path(self.vault), encoding='utf-8') as f:
            queue = json.load(f)
        entry = queue['links'][_WALL_URL]
        # reset: the consumed stamp is cleared (the owner may deliver
        # again) and the requeue is stamped.
        self.assertFalse(entry.get('consumed'))
        self.assertTrue(entry.get('requeued'))

    def test_non_http_urls_are_never_queued(self):
        report = hd.enqueue_hand_delivery(
            self.vault, ['not-a-url', 'ftp://x.example.net/a'],
            log=lambda *a, **k: None)
        self.assertEqual(report['added'], 0)

    def test_corrupt_queue_json_is_tolerated(self):
        folder = hd.hand_delivery_dir(self.vault)
        os.makedirs(folder, exist_ok=True)
        with open(hd.queue_path(self.vault), 'w', encoding='utf-8') as f:
            f.write('{ not json !!')
        report = hd.enqueue_hand_delivery(self.vault, [_WALL_URL],
                                          log=lambda *a, **k: None)
        self.assertEqual(report['added'], 1)  # the corrupt file is replaced

    def test_dry_run_records_without_writing(self):
        dryrun.enable()
        try:
            report = hd.enqueue_hand_delivery(
                self.vault, [_WALL_URL], log=lambda *a, **k: None)
            self.assertEqual(report['added'], 1)
            self.assertFalse(os.path.exists(hd.queue_path(self.vault)))
            self.assertGreaterEqual(dryrun.entry_count(), 2)
        finally:
            dryrun.disable()

    def test_empty_vault_is_a_silent_noop(self):
        report = hd.enqueue_hand_delivery('', [_WALL_URL],
                                          log=lambda *a, **k: None)
        self.assertEqual(report['added'], 0)


# ---------------------------------------------------------------------------
# 4. the real Chrome
# ---------------------------------------------------------------------------

class TestOpenInChrome(_HandCase):

    def test_loopback_never_opens_a_browser(self):
        with mock.patch.object(hd, 'find_chrome', return_value='/fake/chrome'), \
                mock.patch.object(hd.subprocess, 'Popen') as popen, \
                mock.patch.object(hd.webbrowser, 'open') as wopen:
            out = hd.open_in_chrome('http://127.0.0.1:3000/x')
        self.assertEqual(out, '')
        popen.assert_not_called()
        wopen.assert_not_called()

    def test_found_chrome_launches_detached(self):
        with mock.patch.object(hd, 'find_chrome',
                               return_value='/fake/chrome'), \
                mock.patch.object(hd.subprocess, 'Popen') as popen:
            out = hd.open_in_chrome(_WALL_URL)
        self.assertEqual(out, 'chrome')
        popen.assert_called_once()
        argv = popen.call_args[0][0]
        self.assertEqual(argv, ['/fake/chrome', _WALL_URL])

    def test_system_browser_is_the_honest_fallback(self):
        with mock.patch.object(hd, 'find_chrome', return_value=None), \
                mock.patch.object(hd.subprocess, 'Popen') as popen, \
                mock.patch.object(hd.webbrowser, 'open',
                                  return_value=True) as wopen:
            out = hd.open_in_chrome(_WALL_URL)
        self.assertEqual(out, 'browser')
        popen.assert_not_called()
        wopen.assert_called_once_with(_WALL_URL)

    def test_a_launch_failure_never_raises(self):
        with mock.patch.object(hd, 'find_chrome',
                               return_value='/fake/chrome'), \
                mock.patch.object(hd.subprocess, 'Popen',
                                  side_effect=OSError('nope')):
            out = hd.open_in_chrome(_WALL_URL)  # must not raise
        self.assertEqual(out, '')

    def test_finder_reads_the_windows_candidates(self):
        with mock.patch.object(os, 'name', 'nt'), \
                mock.patch.object(os.path, 'isfile',
                                  side_effect=lambda p: p.endswith(
                                      'chrome.exe')):
            found = hd.find_chrome()
        self.assertTrue(found and found.endswith('chrome.exe'))


# ---------------------------------------------------------------------------
# 5. the consume side
# ---------------------------------------------------------------------------

class TestConsume(_HandCase):

    def test_delivered_page_is_collected_and_consumed(self):
        self.queue_file(_WALL_URL)
        path = self.deliver(_WALL_URL)
        self.assertEqual([d['url'] for d in
                          hd.collect_delivered(self.vault)], [_WALL_URL])
        taken = hd.consume_delivered(self.vault)
        self.assertEqual(len(taken), 1)
        self.assertEqual(taken[0]['url'], _WALL_URL)
        self.assertIn(b'Hand-saved', taken[0]['body'])
        # the FILE stays — it is the record…
        self.assertTrue(os.path.isfile(path))
        # …and the queue is stamped consumed
        with open(hd.queue_path(self.vault), encoding='utf-8') as f:
            queue = json.load(f)
        self.assertTrue(queue['links'][_WALL_URL].get('consumed'))
        # a second collect sees nothing (already consumed)
        self.assertEqual(hd.collect_delivered(self.vault), [])

    def test_missing_file_is_not_delivered(self):
        self.queue_file(_WALL_URL)
        self.assertEqual(hd.collect_delivered(self.vault), [])

    def test_empty_page_keeps_waiting(self):
        self.queue_file(_WALL_URL)
        self.deliver(_WALL_URL, body=b'   ')
        self.assertEqual(hd.consume_delivered(self.vault), [])
        with open(hd.queue_path(self.vault), encoding='utf-8') as f:
            queue = json.load(f)
        self.assertFalse(queue['links'][_WALL_URL].get('consumed'))

    def test_orphan_files_are_ignored(self):
        folder = hd.hand_delivery_dir(self.vault)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, 'random.html'), 'wb') as f:
            f.write(b'<html></html>')
        self.assertEqual(hd.collect_delivered(self.vault), [])
        self.assertEqual(hd.consume_delivered(self.vault), [])

    def test_dry_run_takes_the_pages_without_stamping(self):
        self.queue_file(_WALL_URL)
        self.deliver(_WALL_URL)
        dryrun.enable()
        try:
            taken = hd.consume_delivered(self.vault)
        finally:
            dryrun.disable()
        self.assertEqual(len(taken), 1)
        with open(hd.queue_path(self.vault), encoding='utf-8') as f:
            queue = json.load(f)
        self.assertFalse(queue['links'][_WALL_URL].get('consumed'))

    def test_hand_fetch_result_is_a_real_full_result_with_the_story(self):
        res = hd.hand_fetch_result(_WALL_URL, b'<html></html>',
                                   _WALL_REASON)
        self.assertTrue(res.ok)
        self.assertEqual(res.status, 'full')
        self.assertIn("hand-delivered via the owner's real Chrome",
                      res.reason)
        self.assertIn(_WALL_REASON, res.reason)


# ---------------------------------------------------------------------------
# 6. the master-table gesture
# ---------------------------------------------------------------------------

class TestTableGesture(_HandCase):

    def _write_table(self, rows):
        wp.write_decommission_candidates(
            self.vault, [r['url'] for r in rows], source='retry backlog',
            log=lambda *a, **k: None,
            notes={r['url']: r.get('notes', '') for r in rows})
        path = wp.decommission_table_path(self.vault)
        with open(path, encoding='utf-8') as f:
            lines = f.read().splitlines()
        for r in rows:
            for i, line in enumerate(lines):
                if r['url'] in line and line.startswith('| '):
                    parts = line.split('|')
                    parts[6] = f" {r['status']} "
                    lines[i] = '|'.join(parts)
                    break
        from gitcurator.core.storage import atomic_write_text
        atomic_write_text(path, '\n'.join(lines) + '\n')

    def test_hand_status_is_the_gesture(self):
        self.assertTrue(hd._status_is_hand('🖐 hand'))
        self.assertTrue(hd._status_is_hand('Hand-deliver this one'))
        self.assertFalse(hd._status_is_hand('unreviewed'))
        self.assertFalse(hd._status_is_hand(''))
        self.assertFalse(hd._status_is_hand('♻️ revived'))
        self.assertFalse(hd._status_is_hand('✅ reviewed'))

    def test_hand_gesture_queues_but_never_retires(self):
        self.db.enqueue_retry(_WALL_URL, _WALL_REASON)
        self._write_table([{'url': _WALL_URL, 'status': '🖐 hand',
                            'notes': _WALL_REASON}])
        report = wp.consume_decommission_table(
            self.db, self.vault, log=lambda *a, **k: None)
        self.assertEqual(report['handed'], 1)
        self.assertEqual(report['dead'], 0)
        # NOT a retirement: no dismissal, the retry row stays…
        self.assertFalse(self.db.is_dismissed(_WALL_URL))
        self.assertIsNotNone(self.db.retry_row(_WALL_URL))
        # …the row is stamped…
        with open(wp.decommission_table_path(self.vault),
                  encoding='utf-8') as f:
            table = f.read()
        self.assertIn('🖐 hand — queued', table)
        # …and the queue.json carries the link
        with open(hd.queue_path(self.vault), encoding='utf-8') as f:
            queue = json.load(f)
        self.assertIn(_WALL_URL, queue['links'])

    def test_second_pass_never_restamps(self):
        self._write_table([{'url': _WALL_URL, 'status': '🖐 hand'}])
        wp.consume_decommission_table(self.db, self.vault,
                                      log=lambda *a, **k: None)
        with open(wp.decommission_table_path(self.vault),
                  encoding='utf-8') as f:
            first = f.read()
        report = wp.consume_decommission_table(
            self.db, self.vault, log=lambda *a, **k: None)
        self.assertEqual(report['handed'], 0)
        with open(wp.decommission_table_path(self.vault),
                  encoding='utf-8') as f:
            self.assertEqual(f.read(), first)

    def test_death_beats_hand_when_a_cell_says_both(self):
        self._write_table([{'url': _WALL_URL,
                            'status': '🪦 dead + hand'}])
        report = wp.consume_decommission_table(
            self.db, self.vault, log=lambda *a, **k: None)
        self.assertEqual(report['dead'], 1)
        self.assertEqual(report['handed'], 0)
        self.assertTrue(self.db.is_dismissed(_WALL_URL))

    def test_header_grammar_names_the_door(self):
        wp.write_decommission_candidates(self.vault, [_WALL_URL],
                                         log=lambda *a, **k: None)
        with open(wp.decommission_table_path(self.vault),
                  encoding='utf-8') as f:
            header = f.read()
        self.assertIn('🖐 hand — the fourth door', header)


# ---------------------------------------------------------------------------
# 7. the pipeline
# ---------------------------------------------------------------------------

class _PipelineCase(_HandCase):

    def setUp(self):
        super().setUp()
        self.in_vault = set()
        self.logs = []

    def make_pipeline(self, fetch, config=None):
        cfg = {'website_vault_path': self.vault,
               'web_domain_delay_s': 0}
        cfg.update(config or {})
        return wp.WebsitePipeline(
            config=cfg, llm_call=_FakeLLM(),
            vault_index_has=lambda u: u in self.in_vault,
            state=self.db, fetch_fn=fetch,
            rate_limiter=_FakeLimiter(),
            log=lambda m, l='info': self.logs.append((l, m)))

    def all_logs(self):
        return '\n'.join(m for (_, m) in self.logs)


class TestPipelineFourthDoor(_PipelineCase):

    def test_delivered_page_answers_before_any_machine_door(self):
        self.db.enqueue_retry(_WALL_URL, _WALL_REASON)
        self.queue_file(_WALL_URL)
        self.deliver(_WALL_URL)
        fetch = _FakeFetch(_res('HTTP 403 — bot defense', 403,
                                'blocked_bot'))
        pipe = self.make_pipeline(fetch)
        res = pipe.process_link(_WALL_URL)
        # the machine fetcher was NEVER asked…
        self.assertEqual(fetch.calls, [])
        # …the note is a real processed note…
        self.assertEqual(res['outcome'], 'processed')
        self.assertEqual(res['fetch_status'], 'full')
        self.assertTrue(os.path.exists(res['note_path']))
        # …the story is told…
        self.assertIn('hand-delivered', self.all_logs())
        # …and the retry row resolved (a successful fetch)
        self.assertIsNone(self.db.retry_row(_WALL_URL))

    def test_run_pulls_delivered_pages_into_the_batch(self):
        other = 'https://fresh.example.net/page'
        self.queue_file(_WALL_URL)
        self.deliver(_WALL_URL)
        fetch = _FakeFetch(_ok(other))
        pipe = self.make_pipeline(fetch)
        results = pipe.run([other])
        urls = [r['url'] for r in results]
        self.assertIn(_WALL_URL, urls)
        self.assertEqual(res_walled := [r for r in results
                                        if r['url'] == _WALL_URL][0]
                         ['outcome'], 'processed')
        self.assertIn('join this batch', self.all_logs())

    def test_walled_failure_gains_the_fourth_door_hint(self):
        fetch = _FakeFetch(_res(_WALL_REASON, 403, 'blocked_bot'))
        pipe = self.make_pipeline(fetch)
        res = pipe.process_link(_WALL_URL)
        self.assertEqual(res['outcome'], 'review')
        self.assertIn('fourth door', res['error'])
        self.assertIn('fourth door', self.all_logs())
        with open(res['note_path'], encoding='utf-8') as f:
            note = f.read()
        self.assertIn('fourth door', note)
        # the retry row's last_error carries it too (the picker's data)
        row = self.db.retry_row(_WALL_URL)
        self.assertIn('fourth door', row['last_error'])

    def test_resource_truths_get_no_hint(self):
        fetch = _FakeFetch(_res('HTTP 404 — the page is gone', 404,
                                'dead'))
        pipe = self.make_pipeline(fetch)
        res = pipe.process_link('https://gone.example.org/x')
        self.assertNotIn('fourth door', res['error'])

    def test_opt_out_kills_the_whole_door(self):
        self.db.enqueue_retry(_WALL_URL, _WALL_REASON)
        self.queue_file(_WALL_URL)
        self.deliver(_WALL_URL)
        fetch = _FakeFetch(_res(_WALL_REASON, 403, 'blocked_bot'))
        pipe = self.make_pipeline(fetch,
                                  config={'web_hand_delivery': False})
        res = pipe.process_link(_WALL_URL)
        # the machine door was asked (the walled answer)…
        self.assertEqual(fetch.calls, [_WALL_URL])
        self.assertEqual(res['outcome'], 'review')
        # …no hint…
        self.assertNotIn('fourth door', res['error'])
        # …and the queue row is untouched
        with open(hd.queue_path(self.vault), encoding='utf-8') as f:
            queue = json.load(f)
        self.assertFalse(queue['links'][_WALL_URL].get('consumed'))


# ---------------------------------------------------------------------------
# 8. release bookkeeping
# ---------------------------------------------------------------------------

class TestReleaseBookkeeping(unittest.TestCase):
    """The fourth door ships as a real release: the VERSION pin, the
    CHANGELOG beat, the CI registration, the AGENTS.md listing, the
    config key documented where every key is."""

    def setUp(self):
        self.root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), 'r',
                  encoding='utf-8') as f:
            return f.read()

    def test_version_is_0480(self):
        self.assertEqual(self._read('VERSION').strip(), '0.56.0')

    def test_changelog_has_the_beat(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.48.0]', text)
        self.assertIn('fourth door', text.lower())

    def test_ci_runs_this_module(self):
        text = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('tests.test_handdelivery', text)

    def test_agents_md_lists_this_module(self):
        text = self._read('AGENTS.md')
        self.assertIn('test_handdelivery', text)

    def test_config_documents_the_key(self):
        self.assertIn('"web_hand_delivery": true',
                      self._read('app', 'config.example.json'))

    def test_compile_list_has_the_module(self):
        text = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('gitcurator/core/hand_delivery.py', text)


if __name__ == '__main__':
    unittest.main()

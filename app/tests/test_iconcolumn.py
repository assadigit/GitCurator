"""tests/test_iconcolumn.py — v0.52.0, the icon column speaks; the
hand is Chrome's own.

The owner's report (session, verbatim): "Please pay attention, see I
set hand emoji, but those links didn't refetched using scrapping
manually on chrome." His screenshot (b.png) showed the master note
with FOUR columns (``# | Date | URL | Domain``) and the verdict
emojis set in the FIRST cell — 🖐️ on tgstat.com / dehashed.com /
flightradar24.com, 💀 on darkwebdaily.live / tor2web.org — the shape
the legend's own emoji lines read like. The parse demanded all seven
columns and read the verdict only from the Status cell, so every one
of those rows was INVISIBLE: the 🖐 rows never queued, the 💀 rows
never buried, and even a caught-up sync said "everything is up to
date". And the 🖐 gesture, when the app DID see one, only asked the
owner to press Ctrl+S — the fifth door (the app's own Chrome
scraping) never fired for it.

Covered here (unit law: temp vaults, injected seams, zero sockets —
no browser ever launches):

* the parse — ``_parse_decommission_rows``: a FOUR-column row exists
  (URL = column 4 in every shape), the gesture is the # cell + the
  Status cell combined (a plain '-' # cell adds nothing — the
  app-written rows keep their exact old strings), a hand-made shape
  finds its URL by scan, sub-4-column junk is still skipped;
* the verdicts from the icon cell: 💀/☠️ over 'unreviewed' is DEAD,
  ✅ over 'unreviewed' is REVIEWED (the tick beats the default word),
  🖐 AND ✋ (U+270B, the codepoint the owner's editor gave him) and
  the bare word 'hand' are HAND, ♻️ is REVIVED — and a hand icon is
  never 'waiting' (the machine retries own " - ", not the gesture);
* the table shape — ``_ensure_table_shape``: a trimmed table gets its
  seven columns back (header, separator, padded rows — the owner's
  emoji cells untouched, idempotent), a full table is a byte-stable
  no-op;
* the consume — a 4-column table with 💀 and 🖐 icon rows: the skull
  row is dismissed (the burial), the hand row is ENQUEUED and
  stamped 'queued' (the stamp visible — the rows were padded), and
  the log says the app's own Chrome takes them (no Ctrl+S demand);
* the hand scan — ``scan_master_hand_rows``: icon or Status cell,
  delivered/consumed rows never re-asked, dead/reviewed/revived
  outrank, loopback never opens, dedupe by canonical;
* the queue view — ``pending_hand_links``: the fifth door's to-do
  list (queued, not consumed, page not landed);
* the fifth door answers the gesture — ``deliver_pages_via_chrome``
  on a hand-queued link (injected fetcher): the page lands, and
  ``take_hand_delivered`` turns it into a REAL fetch result;
* the caught-up routing — ``_after_sync_fetch``: hand rows start the
  Chrome delivery and "All caught up" is never said while they wait;
  waiting rows and hand rows BOTH fire; a bare hero (no
  collaborator) keeps the old law;
* release bookkeeping — VERSION, CHANGELOG, ci.yml, AGENTS.md.

No PyQt import at module level (the libEGL-less sandbox rule) — the
hero-routing class imports the mixin under the same try/except guard
the queue-fix suite uses.
"""

import json
import os
import shutil
import tempfile
import types
import unittest

from gitcurator.core import dryrun
from gitcurator.core import website_pipeline as wp
from gitcurator.core import hand_delivery as hd
from gitcurator.core import chrome_tabs as ct
from gitcurator.core import web_fetch as _web_fetch


_URL = 'https://tgstat.example.net/'
_URL2 = 'https://dehashed.example.net/'
_URL3 = 'https://darkwebdaily.example.net/'
_CANON = wp.normalize_website_url(_URL)
_ERR = 'HTTP 403 — bot defense (server: cloudflare)'


class _IconCase(unittest.TestCase):
    """Temp vault + state DB, cleaned up (the fifth-door pattern)."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='icon-')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(os.path.join(self.vault, '_review'), exist_ok=True)
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))
        self.logs = []

    def tearDown(self):
        dryrun.disable()
        dryrun.clear()
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def log(self, m, l='info'):
        self.logs.append((l, m))

    def all_logs(self):
        return '\n'.join(m for (_, m) in self.logs)

    # -- table writers ---------------------------------------------------

    def write_four_col_table(self, rows):
        """rows: [(icon, url)] → THE OWNER'S SHAPE (his screenshot):
        ``| # | Date | URL | Domain |`` with the emoji in the # cell."""
        lines = ['# Review Master Table — decommission or approve',
                 '',
                 '| # | Date | URL | Domain |',
                 '|---|------|-----|--------|']
        for icon, url in rows:
            domain = url.split('/')[2] if url.count('/') >= 2 else 'x'
            lines.append(f"| {icon} | 2026-10-07 | {url} | {domain} |")
        path = wp.decommission_table_path(self.vault)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        return path

    def write_full_table(self, rows):
        """rows: [(url, status, notes)] → the app's own 7-column
        grammar (the writer's law)."""
        lines = ['| # | Date | URL | Domain | Source | Status | Notes |',
                 '|---|------|-----|--------|--------|--------|-------|']
        for url, status, notes in rows:
            domain = url.split('/')[2] if url.count('/') >= 2 else 'x'
            lines.append(f"| - | 2026-10-07 | {url} | {domain} "
                         f"| test | {status} | {notes} |")
        path = wp.decommission_table_path(self.vault)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        return path

    def table_text(self):
        with open(wp.decommission_table_path(self.vault),
                  encoding='utf-8') as f:
            return f.read()

    def queue(self):
        p = hd.queue_path(self.vault)
        if not os.path.isfile(p):
            return {'links': {}}
        with open(p, encoding='utf-8') as f:
            return json.load(f)


# ---------------------------------------------------------------------------
# 1. the parse — the four-column law
# ---------------------------------------------------------------------------

class TestParseFourColumns(_IconCase):

    def test_a_four_column_row_exists(self):
        self.write_four_col_table([('🖐', _URL)])
        rows = wp._parse_decommission_rows(
            wp.decommission_table_path(self.vault))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['url'], _URL)
        self.assertEqual(rows[0]['icon'], '🖐')
        # the gesture is the combined text (icon + Status cell)
        self.assertEqual(rows[0]['status'], '🖐')

    def test_the_url_stays_column_four_in_both_shapes(self):
        self.write_four_col_table([('✋', _URL)])
        path = wp.decommission_table_path(self.vault)
        rows = wp._parse_decommission_rows(path)
        self.assertEqual(rows[0]['url'], _URL)
        self.write_full_table([(_URL, 'unreviewed', _ERR)])
        rows = wp._parse_decommission_rows(path)
        self.assertEqual(rows[0]['url'], _URL)

    def test_a_plain_dash_icon_adds_nothing(self):
        # the app writes '-' in the # cell — its rows keep the exact
        # old Status strings (the minimal-diff law)
        self.write_full_table([(_URL, '🪦 dead', '')])
        rows = wp._parse_decommission_rows(
            wp.decommission_table_path(self.vault))
        self.assertEqual(rows[0]['status'], '🪦 dead')
        self.assertEqual(rows[0]['icon'], '')

    def test_an_icon_over_a_status_cell_combines(self):
        self.write_full_table([(_URL, 'unreviewed', _ERR)])
        # simulate the owner editing the # cell of an app row
        path = wp.decommission_table_path(self.vault)
        with open(path, encoding='utf-8') as f:
            text = f.read().replace('| - |', '| 💀 |', 1)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        rows = wp._parse_decommission_rows(path)
        self.assertEqual(rows[0]['icon'], '💀')
        self.assertIn('💀', rows[0]['status'])
        self.assertIn('unreviewed', rows[0]['status'])

    def test_a_hand_made_shape_finds_its_url(self):
        path = wp.decommission_table_path(self.vault)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f"| x | note | {_URL2} | something |\n")
        rows = wp._parse_decommission_rows(path)
        self.assertEqual(rows[0]['url'], _URL2)

    def test_sub_four_column_junk_is_still_skipped(self):
        path = wp.decommission_table_path(self.vault)
        with open(path, 'w', encoding='utf-8') as f:
            f.write("| short | row |\n| 🖐 | no url here | x |\n")
        rows = wp._parse_decommission_rows(path)
        self.assertEqual(rows, [])


# ---------------------------------------------------------------------------
# 2. the verdicts read the icon cell (pure)
# ---------------------------------------------------------------------------

class TestIconVerdicts(unittest.TestCase):

    def test_the_skull_icon_over_the_default_is_dead(self):
        # 💀 over 'unreviewed' — the owner's exact screenshot shape
        self.assertTrue(wp._status_is_dead('💀 unreviewed'))
        self.assertTrue(wp._status_is_dead('☠️ unreviewed'))
        self.assertFalse(wp._status_is_waiting('💀 unreviewed'))
        self.assertFalse(wp._status_is_reviewed('💀 unreviewed'))

    def test_the_tick_icon_over_the_default_is_reviewed(self):
        # the word 'reviewed' hides inside 'unreviewed' — the tick
        # marker must win over the other cell's default
        self.assertTrue(wp._status_is_reviewed('✅ unreviewed'))
        self.assertFalse(wp._status_is_waiting('✅ unreviewed'))
        # the default alone still means nothing
        self.assertFalse(wp._status_is_reviewed('unreviewed'))
        # the bare word still counts (the old law)
        self.assertTrue(wp._status_is_reviewed('reviewed by me'))

    def test_both_hand_codepoints_are_hand(self):
        # 🖐 (U+1F590) and ✋ (U+270B) render near-identically — the
        # owner's editor gave him one of them; both must queue
        for icon in ('🖐', '✋', '🫱'):
            self.assertTrue(hd._status_is_hand(icon + ' unreviewed'),
                            repr(icon))
            self.assertTrue(hd._status_is_hand(icon), repr(icon))
        # the bare word the legend itself prints
        self.assertTrue(hd._status_is_hand('hand'))
        self.assertTrue(hd._status_is_hand('unreviewed hand'))

    def test_a_hand_icon_is_never_waiting(self):
        for icon in ('🖐', '✋'):
            self.assertFalse(wp._status_is_waiting(icon + ' unreviewed'))
            self.assertFalse(wp._status_is_waiting(icon + ' - '))
            self.assertFalse(wp._status_is_waiting(icon))

    def test_the_plain_default_still_waits(self):
        self.assertTrue(wp._status_is_waiting('unreviewed'))
        self.assertTrue(wp._status_is_waiting(' - '))
        self.assertTrue(wp._status_is_waiting(''))

    def test_revived_still_beats_a_hand_word(self):
        self.assertFalse(hd._status_is_hand('♻️ revived — hand typed too'))
        self.assertFalse(hd._status_is_hand('restored by hand'))
        self.assertTrue(wp._status_is_revived('♻️ hand'))

    def test_dead_beats_hand_when_a_cell_says_both(self):
        # the precedence law, unchanged: death is the stronger
        # sentence — every call site (the consume's pass order, the
        # hand scan) checks death BEFORE the hand gesture, so a cell
        # that says both is a DEAD cell
        self.assertTrue(wp._status_is_dead('💀 hand'))
        self.assertTrue(wp._status_is_dead('hand ☠️ dead'))


# ---------------------------------------------------------------------------
# 3. the table shape — trimmed tables get their grammar back
# ---------------------------------------------------------------------------

class TestTableShape(_IconCase):

    def test_a_trimmed_table_is_restored(self):
        self.write_four_col_table([('🖐', _URL), ('💀', _URL3)])
        changed = wp._ensure_table_shape(
            wp.decommission_table_path(self.vault), log=self.log)
        self.assertTrue(changed)
        text = self.table_text()
        # the canonical header and separator are back
        self.assertIn('| # | Date | URL | Domain | Source | Status | '
                      'Notes |', text)
        # the owner's emoji cells are untouched
        self.assertIn('🖐', text)
        self.assertIn('💀', text)
        # every row is padded to the full width (8 pipe-parts)
        for line in text.splitlines():
            if line.startswith('| ') and 'http' in line:
                self.assertEqual(len(line.split('|')), 9,
                                 repr(line))

    def test_the_restore_is_idempotent(self):
        self.write_four_col_table([('🖐', _URL)])
        path = wp.decommission_table_path(self.vault)
        wp._ensure_table_shape(path, log=self.log)
        with open(path, encoding='utf-8') as f:
            once = f.read()
        self.assertFalse(wp._ensure_table_shape(path, log=self.log))
        with open(path, encoding='utf-8') as f:
            twice = f.read()
        self.assertEqual(once, twice)

    def test_a_full_table_is_a_no_op(self):
        self.write_full_table([(_URL, 'unreviewed', _ERR)])
        path = wp.decommission_table_path(self.vault)
        with open(path, encoding='utf-8') as f:
            before = f.read()
        self.assertFalse(wp._ensure_table_shape(path, log=self.log))
        with open(path, encoding='utf-8') as f:
            after = f.read()
        self.assertEqual(before, after)

    def test_a_missing_table_is_quiet(self):
        self.assertFalse(wp._ensure_table_shape(
            wp.decommission_table_path(self.vault), log=self.log))


# ---------------------------------------------------------------------------
# 4. the consume — a 4-column table's gestures are enforced
# ---------------------------------------------------------------------------

class TestConsumeFourColumns(_IconCase):

    def test_the_skull_icon_row_is_buried(self):
        self.write_four_col_table([('💀', _URL3)])
        report = wp.consume_decommission_table(self.db, self.vault,
                                               log=self.log)
        self.assertEqual(report['dead'], 1)
        self.assertTrue(self.db.is_dismissed(
            wp.normalize_website_url(_URL3)))

    def test_the_hand_icon_row_is_enqueued_and_stamped(self):
        self.write_four_col_table([('✋', _URL)])
        report = wp.consume_decommission_table(self.db, self.vault,
                                               log=self.log)
        self.assertEqual(report['handed'], 1)
        # queue.json has the row (the fourth door's memo)
        self.assertIn(_URL, self.queue().get('links', {}))
        # the row is stamped — and VISIBLE (the table was padded)
        self.assertIn('queued', self.table_text())
        # the log tells the fifth door's truth, not a Ctrl+S demand
        self.assertIn('your real Chrome opens', self.all_logs())
        self.assertNotIn('save the pages there', self.all_logs())

    def test_the_stamped_row_never_re_stamps(self):
        self.write_four_col_table([('🖐', _URL)])
        wp.consume_decommission_table(self.db, self.vault, log=self.log)
        with open(wp.decommission_table_path(self.vault),
                  encoding='utf-8') as f:
            once = f.read()
        report = wp.consume_decommission_table(self.db, self.vault,
                                               log=self.log)
        self.assertEqual(report['handed'], 0)
        with open(wp.decommission_table_path(self.vault),
                  encoding='utf-8') as f:
            twice = f.read()
        self.assertEqual(once, twice)

    def test_a_full_table_with_icons_edits_still_consumes(self):
        # an app-shaped row whose # cell the owner emoji'd
        self.write_full_table([(_URL, 'unreviewed', _ERR)])
        path = wp.decommission_table_path(self.vault)
        with open(path, encoding='utf-8') as f:
            text = f.read().replace('| - |', '| ✋ |', 1)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        report = wp.consume_decommission_table(self.db, self.vault,
                                               log=self.log)
        self.assertEqual(report['handed'], 1)
        self.assertIn(_URL, self.queue().get('links', {}))


# ---------------------------------------------------------------------------
# 5. the hand scan — the fifth door's own to-do list
# ---------------------------------------------------------------------------

class TestScanMasterHandRows(_IconCase):

    def test_icon_hand_rows_come_back(self):
        self.write_four_col_table([('🖐', _URL), ('✋', _URL2),
                                   ('💀', _URL3)])
        rows = wp.scan_master_hand_rows(self.vault)
        self.assertEqual({r['url'] for r in rows}, {_URL, _URL2})

    def test_status_cell_hand_rows_come_back(self):
        self.write_full_table([(_URL, '🖐 hand', ''),
                               (_URL2, ' - ', ''),
                               (_URL3, '💀 dead', '')])
        rows = wp.scan_master_hand_rows(self.vault)
        self.assertEqual([r['url'] for r in rows], [_URL])

    def test_delivered_and_consumed_rows_never_re_ask(self):
        self.write_four_col_table([('🖐', _URL), ('✋', _URL2)])
        # _URL: consumed (the record says the page was taken)
        # _URL2: the page LANDED but no batch consumed it yet
        hd.enqueue_hand_delivery(self.vault, [_URL, _URL2],
                                 log=lambda *a, **k: None)
        queue = self.queue()
        queue['links'][_URL]['consumed'] = '2026-10-07 20:00'
        with open(hd.queue_path(self.vault), 'w', encoding='utf-8') as f:
            json.dump(queue, f)
        sug = hd.suggested_filename(_URL2)
        page = os.path.join(hd.hand_delivery_dir(self.vault), sug)
        os.makedirs(os.path.dirname(page), exist_ok=True)
        with open(page, 'wb') as f:
            f.write(b'<html>landed</html>')
        rows = wp.scan_master_hand_rows(self.vault)
        self.assertEqual(rows, [])

    def test_the_dead_outrank_the_hand(self):
        self.write_four_col_table([('💀', _URL)])
        self.assertEqual(wp.scan_master_hand_rows(self.vault), [])
        self.write_full_table([(_URL, '💀 dead + hand', '')])
        self.assertEqual(wp.scan_master_hand_rows(self.vault), [])

    def test_loopback_never_opens_and_duplicates_dedupe(self):
        loop = 'http://127.0.0.1:9191/x'
        self.write_full_table([(loop, '🖐 hand', ''), (_URL, '🖐 hand', ''),
                               (_URL, '✋ again', '')])
        rows = wp.scan_master_hand_rows(self.vault)
        self.assertEqual([r['url'] for r in rows], [_URL])

    def test_no_table_is_quiet(self):
        self.assertEqual(wp.scan_master_hand_rows(self.vault), [])


# ---------------------------------------------------------------------------
# 6. the queue view — pending_hand_links
# ---------------------------------------------------------------------------

class TestPendingHandLinks(_IconCase):

    def test_queued_undelivered_links_are_pending(self):
        hd.enqueue_hand_delivery(self.vault, [_URL, _URL2],
                                 walls={_URL: _ERR},
                                 log=lambda *a, **k: None)
        pending = hd.pending_hand_links(self.vault)
        self.assertEqual({p['url'] for p in pending}, {_URL, _URL2})
        by_url = {p['url']: p for p in pending}
        self.assertEqual(by_url[_URL]['wall'], _ERR)

    def test_consumed_and_landed_links_are_not_pending(self):
        hd.enqueue_hand_delivery(self.vault, [_URL, _URL2],
                                 log=lambda *a, **k: None)
        queue = self.queue()
        queue['links'][_URL]['consumed'] = '2026-10-07 20:00'
        with open(hd.queue_path(self.vault), 'w', encoding='utf-8') as f:
            json.dump(queue, f)
        page = os.path.join(hd.hand_delivery_dir(self.vault),
                            hd.suggested_filename(_URL2))
        os.makedirs(os.path.dirname(page), exist_ok=True)
        with open(page, 'wb') as f:
            f.write(b'<html>landed</html>')
        self.assertEqual(hd.pending_hand_links(self.vault), [])

    def test_a_broken_queue_reads_empty(self):
        os.makedirs(hd.hand_delivery_dir(self.vault), exist_ok=True)
        with open(hd.queue_path(self.vault), 'w', encoding='utf-8') as f:
            f.write('{not json')
        self.assertEqual(hd.pending_hand_links(self.vault), [])


# ---------------------------------------------------------------------------
# 7. the fifth door answers the gesture (injected fetcher, no browser)
# ---------------------------------------------------------------------------

class TestFifthDoorAnswersTheGesture(_IconCase):

    def _fetcher(self, urls, log=None):
        return [{'url': u, 'ok': True, 'title': 'T',
                 'html': '<html><head><title>Live DOM</title>'
                         '</head><body>' + ('x' * 500) + '</body></html>',
                 'error': ''} for u in urls]

    def test_a_hand_row_delivers_via_chrome_and_consumes(self):
        # the owner's exact shape: the gesture in the # cell
        self.write_four_col_table([('✋', _URL)])
        links = wp.scan_master_hand_rows(self.vault)
        self.assertEqual(len(links), 1)
        report = ct.deliver_pages_via_chrome(
            self.vault, [{'url': links[0]['url'],
                          'error': links[0].get('wall') or _ERR}],
            log=lambda *a, **k: None, _fetcher=self._fetcher)
        self.assertEqual(report['delivered'], 1)
        # the page landed at the suggested filename
        path = os.path.join(hd.hand_delivery_dir(self.vault),
                            hd.suggested_filename(_URL))
        self.assertTrue(os.path.isfile(path))
        # the hand row is ANSWERED now — the scan never re-asks it
        self.assertEqual(wp.scan_master_hand_rows(self.vault), [])
        # the consume side turns it into a REAL fetch result
        res = hd.take_hand_delivered(self.vault, _CANON,
                                     log=lambda *a, **k: None)
        self.assertIsNotNone(res)
        self.assertEqual(res.status, 'full')
        self.assertIn('Chrome', res.reason)

    def test_a_failed_scrape_keeps_the_row_waiting(self):
        self.write_four_col_table([('🖐', _URL)])
        def _failing(urls, log=None):
            return [{'url': u, 'ok': False, 'title': '', 'html': '',
                     'error': 'the page never finished loading in 45s'}
                    for u in urls]
        report = ct.deliver_pages_via_chrome(
            self.vault, [{'url': _URL, 'error': _ERR}],
            log=lambda *a, **k: None, _fetcher=_failing)
        self.assertEqual(report['failed'], 1)
        # the row still waits — the next end-of-run pass (or the
        # owner's own Ctrl+S) is its door
        self.assertEqual(len(wp.scan_master_hand_rows(self.vault)), 1)


# ---------------------------------------------------------------------------
# 8. the caught-up routing (the hero mixin, guarded)
# ---------------------------------------------------------------------------

try:
    from gitcurator.gui.main_window.hero import HeroMixin
    _QT_OK = True
except BaseException:  # noqa: BLE001 — PyQt6 stack unavailable
    _QT_OK = False


@unittest.skipUnless(_QT_OK, "PyQt6 stack unavailable (runs in CI)")
class TestHeroHandRouting(unittest.TestCase):
    """The owner's exact complaint surface: the SYNC that finds nothing
    new must NOT say "All caught up" while 🖐 hand rows wait — it opens
    the Chrome delivery instead. Bare-mixin stubs, the queue-fix
    suite's _hero pattern."""

    class _Txt:
        def __init__(self, s=""):
            self._s = s

        def text(self):
            return self._s

    def _hero(self, waiting=None, hand=None, eyes=0):
        hero = HeroMixin()
        hero.states = []
        hero._bot_queue_urls = []
        hero._bot_queue_pending_websites = []
        hero.import_file = self._Txt("")
        hero.logs = []
        hero.log_message = lambda msg, level="info": hero.logs.append(
            (level, msg))
        hero._set_hero_state = lambda state: hero.states.append(state)
        hero.progress_bar = types.SimpleNamespace(
            setFormat=lambda fmt: setattr(hero, "fmt", fmt))
        hero.progress_count = types.SimpleNamespace(
            setText=lambda t: None, setToolTip=lambda t: None)
        hero._scan_master_waiting = lambda: list(waiting or [])
        hero._master_waiting_eyes = eyes
        hero._master_starts = []
        hero._start_master_retry = lambda: hero._master_starts.append(1)
        hero._scan_master_hand = lambda: list(hand or [])
        hero._chrome_starts = []
        hero._start_chrome_tab_retry = lambda links: \
            hero._chrome_starts.append(list(links))
        hero._log_caught_up_state = lambda: hero.logs.append(
            ("info", "CAUGHT_UP_STATE"))
        return hero

    def test_hand_rows_start_the_chrome_delivery_not_the_claim(self):
        hero = self._hero(hand=[{'url': _URL, 'wall': _ERR}])
        hero._after_sync_fetch("bot_check", {"success": True})
        self.assertEqual(len(hero._chrome_starts), 1)
        self.assertEqual(hero._chrome_starts[0][0]['url'], _URL)
        self.assertTrue(any("hand row(s)" in m for _, m in hero.logs))
        # the empty-state claim is NEVER made while hand rows wait
        self.assertFalse(any("All caught up" in m for _, m in hero.logs))
        self.assertFalse(any(m == "CAUGHT_UP_STATE"
                             for _, m in hero.logs))

    def test_waiting_and_hand_rows_both_fire(self):
        hero = self._hero(waiting=[{'url': _URL2, 'kind': 'fetch'}],
                          hand=[{'url': _URL, 'wall': _ERR}])
        hero._after_sync_fetch("bot_check", {"success": True})
        self.assertEqual(hero._master_starts, [1])
        self.assertEqual(len(hero._chrome_starts), 1)
        self.assertFalse(any("All caught up" in m for _, m in hero.logs))

    def test_no_hand_rows_keeps_the_old_law(self):
        hero = self._hero(waiting=[], hand=[])
        hero._after_sync_fetch("bot_check", {"success": True})
        self.assertEqual(hero._chrome_starts, [])
        self.assertEqual(hero.states, ["sync"])
        self.assertTrue(any("All caught up" in m for _, m in hero.logs))
        self.assertTrue(any(m == "CAUGHT_UP_STATE"
                            for _, m in hero.logs))

    def test_a_bare_hero_without_the_collaborators_is_unchanged(self):
        # the guarded-call idiom: the mixin-level flow (and its
        # headless test stubs) predate the collaborators — the old
        # caught-up law must hold untouched
        hero = HeroMixin()
        hero.states = []
        hero._bot_queue_urls = []
        hero._bot_queue_pending_websites = []
        hero.import_file = self._Txt("")
        hero.logs = []
        hero.log_message = lambda msg, level="info": hero.logs.append(
            (level, msg))
        hero._set_hero_state = lambda state: hero.states.append(state)
        hero.progress_bar = types.SimpleNamespace(setFormat=lambda f: None)
        hero.progress_count = types.SimpleNamespace(
            setText=lambda t: None, setToolTip=lambda t: None)
        hero._log_caught_up_state = lambda: None
        hero._after_sync_fetch("bot_check", {"success": True})
        self.assertEqual(hero.states, ["sync"])
        self.assertTrue(any("All caught up" in m for _, m in hero.logs))

    def test_a_delivery_start_failure_is_honest_not_fatal(self):
        hero = self._hero(hand=[{'url': _URL, 'wall': _ERR}])

        def _boom(links):
            raise RuntimeError("no chrome today")
        hero._start_chrome_tab_retry = _boom
        hero._after_sync_fetch("bot_check", {"success": True})
        # the failure is named with its door, the flow survives
        self.assertTrue(any("could not start" in m for _, m in hero.logs))


# ---------------------------------------------------------------------------
# 9. release bookkeeping
# ---------------------------------------------------------------------------

class TestReleaseBookkeeping(unittest.TestCase):

    def setUp(self):
        self.root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), 'r',
                  encoding='utf-8') as f:
            return f.read()

    def test_version_is_0520(self):
        self.assertEqual(self._read('VERSION').strip(), '0.54.0')

    def test_changelog_mentions_the_law(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.52.0]', text)
        self.assertIn('scan_master_hand_rows', text)

    def test_ci_lists_this_module(self):
        self.assertIn('tests.test_iconcolumn',
                      self._read('.github', 'workflows', 'ci.yml'))

    def test_agents_md_lists_this_module(self):
        self.assertIn('test_iconcolumn', self._read('AGENTS.md'))


if __name__ == '__main__':
    unittest.main()

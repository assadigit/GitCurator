"""tests/test_trashdoor.py — v0.62.0 THE TRASH DOOR.

The owner's ask (session, verbatim): "the problem of not being able to
delete some notes and never-fetch them again exists. What if, we create
a folder called trash, every note which goes to trash will be deleted
from vault and never fetch again."

The answer keeps every law: the trash move is not a second grammar —
it is the THIRD door of the ONE deletion grammar (after the note's own
tags and the master-table gesture). A note the owner moves into a
root-level ``Trash`` folder (any spelling, its subfolders too) reads as
carrying the delete verdict: it rides the SAME gate
(``scan_pending_banishments`` — nothing is deleted without the owner's
confirm), the SAME enforcement (``banish_marked_notes`` — the note
leaves the library for ``.trash/banished``, the URL is dismissed with
the banished reason so it is NEVER fetched again, the ledger row is
forgotten, the master table holds the ♻️-revivable record row whose
Source column names the door), and the SAME sacred law (a hand-written
note in Trash is kept with a warning — the app never deletes what it
did not write). The vault scan's side of the contract: the waiting room
is counted honestly (``trash_notes``) but never shown to the LLM, and
no plan may ever propose moving a note INTO the trash.

Covered here (zero network, zero Qt — the hermetic law):

* the predicate — root-level ``Trash`` in any spelling and its
  subfolders are the door; a deep ``SomeDir/Trash`` never is;
* the detection — placement fires with no tag at all, the tag doors
  win the marker when the owner marked AND moved, hand-written notes
  ride with ``app_owned: False``, a sourceless note never fires, and a
  note already resting in the hidden ``.trash`` quarantine is never
  re-found;
* the gate's eyes — trash items appear deduped with
  ``door: 'trash folder'``;
* the enforcement — the burial (file → ``.trash/banished``, URL
  blacklisted with the trash gesture in the reason, record row's
  Source reads ``Trash folder``), the sacred keep, the dry-run
  rehearsal, the never-fetch hold on a fresh arrival;
* the scan's own laws — the inventory counts the waiting room without
  listing it, ``_sanitize_folder`` rejects the trash as a destination,
  and a plan that tries to file INTO the trash is dropped.
"""

import os
import shutil
import tempfile
import unittest

from gitcurator.core import dryrun
from gitcurator.core import vault_scan as vs
from gitcurator.core import website_pipeline as wp


def _write_note(vault, relpath, url, tags='[design]',
                managed='gitcurator', extra_fm=''):
    """Hand-write a note the way build_website_note shapes it (the
    banishment suite's own factory, verbatim style)."""
    path = os.path.join(vault, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"""---
source: "{url}"
aliases: []
tags: {tags}
category: "Design"
subcategory: "Assets & Resources"
fetch_status: "full"
pricing: "free"
login_required: "no"
date_processed: 2026-10-09
managed_by: "{managed}"
schema_version: "1"
prompt_version: "web-v2"
{extra_fm}---

> [!info] Managed by GitCurator — machine-written note.

# Test Site

> **TL;DR:** A test page about design.
""")
    return path


class _TrashCase(unittest.TestCase):
    """Shared plumbing: temp vault + state db."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='trashdoor-')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(self.vault, exist_ok=True)
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

    def table_text(self):
        path = wp.decommission_table_path(self.vault)
        if not os.path.isfile(path):
            return ''
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()


class TestThePredicate(_TrashCase):

    def test_root_level_any_spelling_and_subfolders(self):
        for rel in ('Trash', 'trash', 'TRASH', 'TrAsH',
                    'Trash/maybe', 'trash/deep/deeper'):
            self.assertTrue(wp._is_trash_folder(rel), rel)

    def test_not_the_door(self):
        for rel in ('', '.', 'SomeDir/Trash', 'Trashy', 'Design',
                    'AI-Domain/Agents', 'trashe', 'trash-x'):
            self.assertFalse(wp._is_trash_folder(rel), rel)

    def test_the_hidden_quarantine_is_not_the_door(self):
        # .trash/banished is the destination, never the door — and it
        # is a dot-folder the walks skip entirely.
        self.assertFalse(wp._is_trash_folder('.trash'))
        self.assertFalse(wp._is_trash_folder('.trash/banished'))


class TestTheDetection(_TrashCase):

    def test_a_moved_note_fires_with_no_tag_at_all(self):
        path = _write_note(self.vault, os.path.join('Trash', 'a.md'),
                           'https://a.example/x')
        items = wp.scan_banished_notes(self.vault, log=self.log)
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it['path'], path)
        self.assertEqual(it['marker'], wp.TRASH_MARKER)
        self.assertEqual(it['door'], 'trash folder')
        self.assertTrue(it['app_owned'])

    def test_any_spelling_and_subfolders(self):
        _write_note(self.vault, os.path.join('trash', 'b.md'),
                    'https://b.example/y')
        _write_note(self.vault,
                    os.path.join('TRASH', 'maybe', 'c.md'),
                    'https://c.example/z')
        items = wp.scan_banished_notes(self.vault, log=self.log)
        self.assertEqual({i['door'] for i in items},
                         {'trash folder'})

    def test_a_tag_wins_the_marker_when_marked_and_moved(self):
        _write_note(self.vault, os.path.join('Trash', 'd.md'),
                    'https://d.example/w', tags='[🗑️]')
        items = wp.scan_banished_notes(self.vault, log=self.log)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['door'], 'note tag')
        self.assertIn('🗑️', items[0]['marker'])

    def test_a_handwritten_note_in_trash_rides_kept(self):
        _write_note(self.vault, os.path.join('Trash', 'mine.md'),
                    'https://e.example/h', managed='a-human')
        items = wp.scan_banished_notes(self.vault, log=self.log)
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0]['app_owned'])

    def test_a_deep_folder_sharing_the_name_never_fires(self):
        _write_note(self.vault,
                    os.path.join('Design', 'Trash', 'e.md'),
                    'https://f.example/t')
        self.assertEqual(wp.scan_banished_notes(self.vault,
                                                log=self.log), [])

    def test_a_sourceless_note_never_fires(self):
        _write_note(self.vault, os.path.join('Trash', 'nolink.md'),
                    '')
        self.assertEqual(wp.scan_banished_notes(self.vault,
                                                log=self.log), [])

    def test_the_hidden_quarantine_is_never_refound(self):
        _write_note(self.vault,
                    os.path.join('.trash', 'banished', 'g.md'),
                    'https://g.example/q', tags='[delete]')
        self.assertEqual(wp.scan_banished_notes(self.vault,
                                                log=self.log), [])

    def test_a_clean_library_note_never_fires(self):
        _write_note(self.vault, os.path.join('Design', 'ok.md'),
                    'https://h.example/ok')
        self.assertEqual(wp.scan_banished_notes(self.vault,
                                                log=self.log), [])


class TestTheGateEyes(_TrashCase):

    def test_trash_items_ride_the_gate_deduped(self):
        _write_note(self.vault, os.path.join('Trash', 'a.md'),
                    'https://a.example/x')
        # the same link ALSO marked the tag way — one pending deletion
        _write_note(self.vault, os.path.join('Design', 'twin.md'),
                    'https://a.example/x', tags='[delete]')
        gate = wp.scan_pending_banishments(self.vault, log=self.log)
        self.assertEqual(len(gate['items']), 1)
        it = gate['items'][0]
        self.assertEqual(it['door'], 'note tag')   # dedup keeps the first
        self.assertIn('a.example', it['canonical'])

    def test_pure_trash_item_carries_its_door(self):
        _write_note(self.vault, os.path.join('Trash', 'solo.md'),
                    'https://solo.example/one')
        gate = wp.scan_pending_banishments(self.vault, log=self.log)
        self.assertEqual(len(gate['items']), 1)
        self.assertEqual(gate['items'][0]['door'], 'trash folder')
        self.assertEqual(gate['items'][0]['marker'], wp.TRASH_MARKER)


class TestTheEnforcement(_TrashCase):

    def test_the_full_burial(self):
        path = _write_note(self.vault, os.path.join('Trash', 'gone.md'),
                           'https://gone.example/bye')
        canonical = wp.normalize_website_url('https://gone.example/bye')
        report = wp.banish_marked_notes(self.db, self.vault,
                                        log=self.log)
        self.assertEqual(report['banished'], 1)
        self.assertEqual(report['notes_moved'], 1)
        # the note left the visible waiting room for the quarantine
        self.assertFalse(os.path.exists(path))
        quarantine = os.path.join(self.vault, '.trash', 'banished')
        self.assertTrue(any(f.startswith('gone.md')
                            for f in os.listdir(quarantine)))
        # the URL is blacklisted with the trash gesture in the reason
        row = self.db.dismissed_row(canonical)
        self.assertIsNotNone(row)
        self.assertIn(wp.TRASH_GESTURE, row['reason'])
        self.assertIn('never fetched again', row['reason'])
        # the record row names the door in its Source column
        table = self.table_text()
        self.assertIn('Trash folder', table)
        self.assertIn('you moved it to the Trash folder', table)
        self.assertIn('♻️', table)          # the revive door exists
        # the log tells the trash story
        self.assertIn('you moved it to the Trash folder', self.all_logs())

    def test_never_fetched_again_holds_for_a_fresh_arrival(self):
        _write_note(self.vault, os.path.join('Trash', 'never.md'),
                    'https://never.example/again')
        canonical = wp.normalize_website_url('https://never.example/again')
        wp.banish_marked_notes(self.db, self.vault, log=self.log)
        # the dismissal IS the never-fetch gate: a re-paste months
        # later is skipped before any fetch (the skip gate's own read).
        row = self.db.dismissed_row(canonical)
        self.assertIn(wp.BANISH_REASON_PREFIX, row['reason'])
        self.assertTrue(self.db.is_dismissed(canonical))

    def test_handwritten_note_is_kept_with_the_warning(self):
        path = _write_note(self.vault, os.path.join('Trash', 'mine.md'),
                           'https://mine.example/own', managed='a-human')
        report = wp.banish_marked_notes(self.db, self.vault,
                                        log=self.log)
        self.assertEqual(report['banished'], 0)
        self.assertEqual(report['kept_handwritten'], 1)
        self.assertTrue(os.path.exists(path))    # the sacred law
        self.assertIn('hand-written', self.all_logs())
        self.assertIn('never deletes what it did not write',
                      self.all_logs())

    def test_dry_run_rehearses_the_burial(self):
        path = _write_note(self.vault, os.path.join('Trash', 'rehearse.md'),
                           'https://rehearse.example/dry')
        dryrun.enable()
        try:
            report = wp.banish_marked_notes(self.db, self.vault,
                                            log=self.log)
        finally:
            dryrun.disable()
            dryrun.clear()
        self.assertEqual(report['banished'], 1)
        # the file never moved — a rehearsal, not a burial
        self.assertTrue(os.path.exists(path))
        self.assertFalse(os.path.exists(
            os.path.join(self.vault, '.trash', 'banished')))
        self.assertIn('REHEARSED', self.all_logs())

    def test_idempotent_the_next_run_is_a_no_op(self):
        _write_note(self.vault, os.path.join('Trash', 'once.md'),
                    'https://once.example/only')
        first = wp.banish_marked_notes(self.db, self.vault, log=self.log)
        self.assertEqual(first['banished'], 1)
        second = wp.banish_marked_notes(self.db, self.vault, log=self.log)
        self.assertEqual(second['banished'], 0)
        self.assertEqual(second['marked'], 0)


class TestTheScanSide(_TrashCase):

    def test_inventory_counts_the_waiting_room_without_listing_it(self):
        _write_note(self.vault, os.path.join('Design', 'lib.md'),
                    'https://lib.example/note')
        _write_note(self.vault, os.path.join('Trash', 'waiting.md'),
                    'https://waiting.example/out')
        _write_note(self.vault, os.path.join('trash', 'sub', 'w2.md'),
                    'https://w2.example/out')
        inv = vs.scan_vault_inventory(self.vault, log=self.log)
        self.assertEqual(inv['trash_notes'], 2)
        self.assertEqual(inv['total_notes'], 1)      # the library only
        self.assertEqual(inv['notes'][0]['file'], 'lib.md')
        self.assertFalse(any(f['rel'].lower().startswith('trash')
                             for f in inv['folders']))
        # the log says the waiting room is seen
        self.assertIn('rest in the Trash folder', self.all_logs())

    def test_the_plan_never_files_into_the_trash(self):
        for bad in ('Trash', 'trash', 'TRASH', 'AI-Domain/Trash'):
            self.assertEqual(vs._sanitize_folder(bad), '', bad)
        self.assertEqual(vs._sanitize_folder('AI-Domain/Agents'),
                         'AI-Domain/Agents')

    def test_a_move_proposed_into_the_trash_is_dropped(self):
        # an ORPHANED note (the vault root) is a listed candidate, so
        # the guest's proposal actually reaches the destination check
        _write_note(self.vault, os.path.join('p.md'),
                    'https://p.example/move')
        inv = vs.scan_vault_inventory(self.vault)
        candidates = vs._move_candidates(inv, 40)
        self.assertTrue(candidates)
        # the LLM (a guest) tries to file INTO the trash — dropped
        data = {'new_folders': ['Trash'],
                'moves': [{'note': 'p.md', 'to': 'Trash'}],
                'summary': 'sneaky'}
        moves, new_folders = vs._validate_moves(data, candidates, inv,
                                                self.log)
        self.assertEqual(moves, [])
        self.assertEqual(new_folders, [])
        self.assertIn('not a legal destination', self.all_logs())

    def test_the_plan_carries_the_waiting_room_count(self):
        _write_note(self.vault, os.path.join('Trash', 'w.md'),
                    'https://w.example/wait')
        _write_note(self.vault, os.path.join('Design', 'd.md'),
                    'https://d.example/lib', tags='[delete]')
        plan = vs.build_scan_plan(self.vault, llm_call=None, log=self.log,
                                  config={})
        self.assertEqual(plan['inventory']['trash_notes'], 1)
        # and the waiting-room note rides the plan's deletions
        doors = {i.get('door') for i in plan['deletions']}
        self.assertEqual(doors, {'note tag', 'trash folder'})


class TestTheModuleKnowsItself(_TrashCase):

    def test_ci_and_agents_know_the_module(self):
        root = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        def _read(*parts):
            with open(os.path.join(root, '..', *parts), 'r',
                      encoding='utf-8') as f:
                return f.read()
        ci = _read('.github', 'workflows', 'ci.yml')
        self.assertIn('tests.test_trashdoor', ci)
        agents = _read('AGENTS.md')
        self.assertIn('tests.test_trashdoor', agents)

    def test_changelog_has_the_door(self):
        root = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        with open(os.path.join(root, '..', 'CHANGELOG.md'), 'r',
                  encoding='utf-8') as f:
            text = f.read()
        self.assertIn('## [0.62.0]', text)
        self.assertIn('Trash', text)


if __name__ == '__main__':
    unittest.main()

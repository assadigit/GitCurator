#!/usr/bin/env python3
"""
test_phase6.py — v0.16.0: the Linking layer (SPEC §4.8 / §6 Phase 6).

Steps, each covered:

  1. Recall hooks — the delimited block (insert/replace/idempotent/CRLF),
     fingerprint invisibility, the sanitizers, field extraction from both
     note formats, the neutral prompt (no about_me.md), the plan/run
     pipeline (dry-run vs apply vs unchanged), the report.
  2. Embeddings — cosine math, the embed TEXT (recall + one-line + tags,
     never full text), Ollama /api/embed (batched) + the legacy
     /api/embeddings fallback, the OpenAI-compatible /v1/embeddings (with
     index reordering), provider routing, the SQLite store, the stale-only
     refresh (all against a stdlib http.server — no real model server).
  3. Candidates + LLM confirm — hand-made vectors → ranked pairs, the
     confirm prompt/parsing, the link store (pending/approved/rejected;
     a pair is suggested at most ONCE), the per-note cap.
  4. The surfaces — the Suggestions note (checkboxes + the URL lines that
     make collect_decisions deterministic), decisions applied, the
     Related (auto) block written into MIRROR copies ONLY (never
     unmarked files, never outside Library/), and the mirror carry-over
     (an approved block survives a mirror re-sync).
  5. The tools — add_recall_hooks (dry-run default, --sample, --apply)
     and build_links (suggest → tick → collect; rejected pairs never
     reappear) end-to-end over synthetic vaults with fake LLM/embedding
     endpoints, everything in tmp dirs.

Headless-safe: QT_QPA_PLATFORM=offscreen, no GUI is ever shown.
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from gitcurator.core import linking as _lk
from gitcurator.core import recall as _rc
from gitcurator.core.embeddings import (EmbeddingError, EmbeddingStore,
                                        build_embedding_call, cosine,
                                        embed_text_for, hash_embed_text,
                                        ollama_embed, openai_embed,
                                        refresh_embeddings)
from gitcurator.core.note_state import (RECALL_END, RECALL_START,
                                        compute_fingerprint)
from gitcurator.core.prompts import PromptError
from gitcurator.core.storage import atomic_write_text


# ---------------------------------------------------------------------------
# Fixtures: realistic notes in both formats + a mirror copy
# ---------------------------------------------------------------------------

def _github_note(name='demo-tool', url='https://github.com/acme/demo-tool',
                 tags='python, cli', tldr='A fast demo tool for testing.'):
    return f"""---
source: {url}
aliases: []
tags: [{tags}]
category: Developer Tools
stars: 120
org: acme
credibility_score: 80/100
date_processed: 2026-09-01
managed_by: gitcurator
schema_version: 2
prompt_version: g3
---

# {name}

> **TL;DR:** {tldr}

**`acme/{name}`** · ⭐ 120 · 🔧 Python

## What is it?
It automates the boring parts of demo datasets so tests stay small.

## How does it work?
A single command walks a folder and emits JSON fixtures.

## Why is it important? (Core Value)
Tests stop depending on hand-written data.

## Key Features & Technologies
- fixture generation
- deterministic output
- zero dependencies

## Difference from Others
Smaller and faster than the big frameworks.

## 🏢 Organization & Credibility
- **Developer:** acme

---
*Source: [GitHub]({url})*
"""


def _website_note(name='Palette Gen', url='https://palette.example.com',
                  best='Use when you need to build a color scheme fast.'):
    return f"""---
source: {url}
aliases: []
tags: [design, colors]
category: Design
subcategory: Color Tools
date_processed: 2026-09-01
managed_by: gitcurator
schema_version: 2
prompt_version: w3
---

# {name}

> **TL;DR:** Generates color palettes from an image.

## One-line description
Generates color palettes from any image.

## Best used for
{best}

## Pricing & sign-up
- Pricing: freemium
- Login required: no

---
*Source: [{url}]({url})*
"""


def _make_vault(root, notes):
    """notes: list of (relpath, content). Returns the vault root."""
    for rel, content in notes:
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_write_text(path, content)
    return root


# ---------------------------------------------------------------------------
# Fake servers: an OpenAI-compatible /embeddings endpoint and an Ollama
# ---------------------------------------------------------------------------

class _FakeEmbedHandler(BaseHTTPRequestHandler):
    """One handler, configurable through server.behavior.

    behavior keys:
      mode        'openai' (POST <base>/embeddings) | 'ollama'
                  (/api/embed) | 'ollama-legacy' (/api/embeddings, one
                  prompt per call) | 'ollama-fallback' (404 on /api/embed,
                  legacy /api/embeddings)
      dim         vector dimension (default 4)
      offset      per-text deterministic offset so vectors differ
      bad_count   return one vector short (error path)
    """

    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        data = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
        b = self.server.behavior
        mode = b.get('mode', 'openai')
        texts = body.get('input') or ([body['prompt']] if 'prompt' in body
                                      else [])
        dim = int(b.get('dim', 4))
        if b.get('bad_count') and len(texts) > 1:
            texts = texts[:-1]          # one vector short
        if mode == 'openai':
            data = [{'object': 'embedding', 'index': len(texts) - 1 - i,
                     'embedding': [float(i + 1) + 0.1 * j
                                   for j in range(dim)]}
                    for i, _t in enumerate(texts)]
            # index says the order; entries are REVERSED on purpose — the
            # client must honor the index, not the list order
            self._send(200, {'object': 'list', 'data': data})
            return
        if mode == 'ollama':
            vectors = [[float(i + 1) + 0.1 * j for j in range(dim)]
                       for i, _t in enumerate(texts)]
            self._send(200, {'model': body.get('model'), 'embeddings':
                             vectors})
            return
        # legacy /api/embeddings — one prompt per call
        i = int(body.get('prompt') == 'second') if body.get(
            'prompt') == 'second' else 0
        i = 1 if body.get('prompt') == 'second' else 0
        self._send(200, {'embedding': [float(i + 1) + 0.1 * j
                                       for j in range(dim)]})


def _start_embed(behavior=None):
    server = HTTPServer(('127.0.0.1', 0), _FakeEmbedHandler)
    server.behavior = dict(behavior or {})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root = f'http://127.0.0.1:{server.server_port}'
    return server, root


def _stop(server):
    server.shutdown()
    server.server_close()


# ---------------------------------------------------------------------------
# 1) The recall block
# ---------------------------------------------------------------------------

class TestRecallBlock(unittest.TestCase):

    def test_insert_get_roundtrip(self):
        note = _github_note()
        out = _rc.insert_recall_block(note, 'Use when you need to test.')
        self.assertTrue(_rc.has_recall_block(out))
        self.assertEqual(_rc.get_recall_sentence(out),
                         'Use when you need to test.')
        self.assertIn(RECALL_START, out)
        self.assertIn(RECALL_END, out)

    def test_idempotent_same_sentence(self):
        note = _rc.insert_recall_block(_github_note(),
                                       'Use when you need to test.')
        again = _rc.insert_recall_block(note, 'Use when you need to test.')
        self.assertEqual(note, again)

    def test_replace_in_place(self):
        note = _rc.insert_recall_block(_github_note(),
                                       'Use when you need to test.')
        note2 = _rc.insert_recall_block(note,
                                        'Use when you need to demo.')
        self.assertEqual(_rc.get_recall_sentence(note2),
                         'Use when you need to demo.')
        # exactly ONE block, and everything outside it is untouched
        self.assertEqual(note2.count(RECALL_START), 1)
        self.assertEqual(_rc.strip_recall_block(note),
                         _rc.strip_recall_block(note2))

    def test_crlf_notes_stay_crlf(self):
        note = _github_note().replace('\n', '\r\n')
        out = _rc.insert_recall_block(note, 'Use when you need to test.')
        self.assertIn('\r\n', out)
        self.assertNotIn('\r\r', out)
        self.assertTrue(_rc.has_recall_block(out))

    def test_fingerprint_ignores_the_block(self):
        note = _github_note()
        before = compute_fingerprint(note)
        hooked = _rc.insert_recall_block(note, 'Use when you need to test.')
        self.assertEqual(before, compute_fingerprint(hooked))
        # and a HUMAN edit after the hook still reads as changed
        self.assertNotEqual(
            before, compute_fingerprint(hooked + '\nhand-written line\n'))

    def test_sanitize_accepts_valid(self):
        ok = 'Use when you need to generate fixture data for tests.'
        self.assertEqual(_rc.sanitize_recall_sentence(ok), ok)
        self.assertEqual(
            _rc.sanitize_recall_sentence('  ' + ok + '  '), ok)

    def test_sanitize_rejects_and_falls_back(self):
        bad = ['Try this amazing tool!!', '', 'multiple\nlines\nhere',
               None, 'Use when you need to\nsecond line']
        for raw in bad:
            self.assertEqual(_rc.sanitize_recall_sentence(raw),
                             _rc.RECALL_PLACEHOLDER, raw)

    def test_sanitize_strips_quotes(self):
        got = _rc.sanitize_recall_sentence(
            '"Use when you need to quote things."')
        self.assertEqual(got, 'Use when you need to quote things.')

    def test_sanitize_trims_rambling(self):
        long_ok = ('Use when you need to ' + 'very ' * 60 + 'long')
        got = _rc.sanitize_recall_sentence(long_ok)
        self.assertTrue(got.startswith('Use when you need to'))
        self.assertLessEqual(len(got), _rc._RECALL_MAX_LEN + 1)


# ---------------------------------------------------------------------------
# 1b) Field extraction + prompt + plan/run
# ---------------------------------------------------------------------------

class TestRecallPipeline(unittest.TestCase):

    def test_extract_github_fields(self):
        fields = _rc.extract_github_fields(_github_note())
        self.assertEqual(fields['REPO_NAME'], 'demo-tool')
        self.assertEqual(fields['TLDR'],
                         'A fast demo tool for testing.')
        self.assertIn('automates the boring parts', fields['SUMMARY'])
        self.assertIn('fixture generation', fields['FEATURES'])
        self.assertEqual(fields['TAGS'], 'python, cli')

    def test_extract_website_fields(self):
        fields = _rc.extract_website_fields(_website_note())
        self.assertEqual(fields['name'], 'Palette Gen')
        self.assertEqual(fields['best_used_for'],
                         'Use when you need to build a color scheme fast.')
        self.assertEqual(fields['tags'], 'design, colors')

    def test_recall_fields_for_both_vaults(self):
        github = _rc.recall_fields_for(
            _rc.insert_recall_block(_github_note(),
                                    'Use when you need to test.'),
            'github')
        self.assertEqual(github['recall'], 'Use when you need to test.')
        web = _rc.recall_fields_for(_website_note(), 'websites')
        self.assertEqual(web['recall'],
                         'Use when you need to build a color scheme fast.')

    def test_prompt_filled_and_neutral(self):
        prompt = _rc.build_recall_prompt(
            _rc.extract_github_fields(_github_note()))
        self.assertIn('demo-tool', prompt)
        self.assertNotIn('{{', prompt)         # no unfilled slot
        self.assertIn('Use when you need to', prompt)
        # NEUTRAL: the recall prompt must NOT personalize (no about_me)
        about = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'about_me.md')
        if os.path.isfile(about):
            with open(about, encoding='utf-8') as f:
                personal = f.read()[:200].strip()
            if personal:
                self.assertNotIn(personal[:40], prompt)

    def test_plan_skips_hooked_and_unmanaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp, [
                ('Dev Tools/a.md', _github_note('a',
                                                'https://github.com/a/a')),
                ('Dev Tools/b.md',
                 _rc.insert_recall_block(
                     _github_note('b', 'https://github.com/b/b'),
                     'Use when you need to b.')),
                ('Dev Tools/unmanaged.md', '# no source here\n'),
            ])
            plan = _rc.plan_recall_hooks(vault)
            self.assertEqual([os.path.basename(p['path'])
                              for p in plan], ['a.md'])
            forced = _rc.plan_recall_hooks(vault, force=True)
            self.assertEqual(len(forced), 2)

    def test_run_dry_run_then_apply(self):
        wrote = []

        def writer(path, content):
            atomic_write_text(path, content)     # real write (recorded)
            wrote.append((path, content))
        llm = lambda messages: json.dumps(      # noqa: E731
            {'best_used_for': 'Use when you need to test things.'})
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp, [
                ('Dev Tools/a.md', _github_note('a',
                                                'https://github.com/a/a')),
            ])
            plan = _rc.plan_recall_hooks(vault)
            results = _rc.run_recall_hooks(plan, llm, apply=False,
                                           writer=writer)
            self.assertEqual(wrote, [])          # dry-run writes nothing
            self.assertEqual(results[0]['sentence'],
                             'Use when you need to test things.')
            results = _rc.run_recall_hooks(plan, llm, apply=True,
                                           writer=writer)
            self.assertEqual(len(wrote), 1)
            with open(wrote[0][0], encoding='utf-8') as f:
                on_disk = f.read()
            self.assertTrue(_rc.has_recall_block(on_disk))
            # re-run with the SAME sentence → unchanged, no rewrite
            results = _rc.run_recall_hooks(
                _rc.plan_recall_hooks(vault, force=True), llm, apply=True,
                writer=writer)
            self.assertTrue(results[0]['unchanged'])
            self.assertEqual(len(wrote), 1)

    def test_run_garbage_llm_gets_placeholder(self):
        llm = lambda messages: 'I cannot answer that.'   # noqa: E731
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp, [
                ('Dev Tools/a.md', _github_note('a',
                                                'https://github.com/a/a')),
            ])
            results = _rc.run_recall_hooks(_rc.plan_recall_hooks(vault),
                                           llm)
            self.assertEqual(results[0]['sentence'],
                             _rc.RECALL_PLACEHOLDER)

    def test_run_single_failure_does_not_abort(self):
        def llm(messages):
            raise RuntimeError('boom')
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp, [
                (f'Dev Tools/n{i}.md',
                 _github_note(f'n{i}', f'https://github.com/x/n{i}'))
                for i in range(3)])
            results = _rc.run_recall_hooks(_rc.plan_recall_hooks(vault),
                                           llm)
            self.assertEqual(len(results), 3)
            self.assertTrue(all('error' in r for r in results))
            report = _rc.recall_report(results, vault, applied=False)
            self.assertIn('ERROR', report)


# ---------------------------------------------------------------------------
# 2) Embeddings
# ---------------------------------------------------------------------------

class TestEmbedMath(unittest.TestCase):

    def test_cosine(self):
        self.assertAlmostEqual(cosine([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(cosine([1, 0], [0, 1]), 0.0)
        self.assertAlmostEqual(cosine([1, 0], [-1, 0]), -1.0)
        self.assertEqual(cosine([], [1]), 0.0)
        self.assertEqual(cosine([1], [1, 2]), 0.0)
        self.assertEqual(cosine([0, 0], [0, 0]), 0.0)

    def test_embed_text_composition(self):
        fields = {'name': 'Palette Gen',
                  'one_line': 'Generates color palettes.',
                  'recall': 'Use when you need to build a scheme.',
                  'tags': 'design, colors'}
        text = embed_text_for(fields)
        self.assertIn('Palette Gen', text)
        self.assertIn('Generates color palettes.', text)
        self.assertIn('Use when you need to build a scheme.', text)
        self.assertIn('tags: design, colors', text)
        # NEVER full text: the whole embedding input stays tiny
        self.assertLessEqual(len(text), 1200)
        self.assertEqual(embed_text_for({}), '')

    def test_hash_changes_with_text(self):
        self.assertNotEqual(hash_embed_text('a'), hash_embed_text('b'))
        self.assertEqual(hash_embed_text('a'), hash_embed_text('a'))


class TestEmbedEndpoints(unittest.TestCase):

    def test_openai_embed_honors_index(self):
        server, root = _start_embed({'mode': 'openai'})
        self.addCleanup(_stop, server)
        vectors = openai_embed(root, 'm', ['first', 'second'])
        self.assertEqual(len(vectors), 2)
        # the fake REVERSES entries and marks them with index — the
        # client must restore INPUT order: 'first' is index 0, whose
        # entry carries the [2.0…] vector
        self.assertEqual(vectors[0], [2.0, 2.1, 2.2, 2.3])
        self.assertEqual(vectors[1], [1.0, 1.1, 1.2, 1.3])

    def test_openai_embed_rejects_wrong_count(self):
        server, root = _start_embed({'mode': 'openai', 'bad_count': True})
        self.addCleanup(_stop, server)
        with self.assertRaises(EmbeddingError):
            openai_embed(root, 'm', ['first', 'second'])

    def test_ollama_embed_batched(self):
        server, root = _start_embed({'mode': 'ollama'})
        self.addCleanup(_stop, server)
        vectors = ollama_embed(root, 'nomic-embed-text', ['first',
                                                          'second'])
        self.assertEqual(len(vectors), 2)
        self.assertEqual(vectors[1][0], 2.0)

    def test_ollama_legacy_fallback(self):
        # /api/embed 404s (old server) → per-text /api/embeddings
        class _LegacyHandler(_FakeEmbedHandler):
            def do_POST(self):
                if self.path.rstrip('/').endswith('/api/embed'):
                    self._send(404, {'error': 'not found'})
                    return
                super().do_POST()
        server = HTTPServer(('127.0.0.1', 0), _LegacyHandler)
        server.behavior = {'mode': 'ollama-legacy'}
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(_stop, server)
        root = f'http://127.0.0.1:{server.server_port}'
        vectors = ollama_embed(root, 'nomic-embed-text', ['first',
                                                          'second'])
        self.assertEqual(len(vectors), 2)
        self.assertEqual(vectors[0][0], 1.0)
        self.assertEqual(vectors[1][0], 2.0)

    def test_build_embedding_call_routes(self):
        # ollama default (nested config), llama.cpp normalization, cloud
        embed, label = build_embedding_call(
            {'llm_provider': 'ollama',
             'ollama': {'base_url': 'http://127.0.0.1:11434',
                        'model': 'llama3'}})
        self.assertIn('Ollama', label)
        self.assertIn('llama3', label)          # chat model as fallback
        embed, label = build_embedding_call(
            {'llm_provider': 'ollama', 'embedding_model': 'nomic-embed'})
        self.assertIn('nomic-embed', label)
        embed, label = build_embedding_call(
            {'llm_provider': 'llamacpp',
             'llamacpp_api_url': '127.0.0.1:8080',
             'llamacpp_model': 'qwen2.5-3b'})
        self.assertIn('llama.cpp', label)
        with self.assertRaises(EmbeddingError):
            build_embedding_call({'llm_provider': 'cloud',
                                  'cloud_api_url': 'https://x/v1',
                                  'cloud_api_key': '',
                                  'cloud_model': ''})


class TestEmbeddingStore(unittest.TestCase):

    def test_roundtrip_and_staleness(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'cache.db')
            store = EmbeddingStore(db)
            store.put('linking', 'https://a', 'm', [0.1, 0.2], 'h1')
            store.put('linking', 'https://b', 'm', [0.3, 0.4], 'h2')
            self.assertEqual(store.count('linking'), 2)
            loaded = store.load('linking', 'm')
            self.assertEqual(loaded['https://a'], ([0.1, 0.2], 'h1'))
            store.put('linking', 'https://a', 'm', [0.9], 'h3')  # upsert
            loaded = store.load('linking', 'm')
            self.assertEqual(loaded['https://a'], ([0.9], 'h3'))
            self.assertEqual(store.drop_model('linking', 'm'), 2)
            self.assertEqual(store.count('linking'), 0)
            store.close()

    def test_refresh_embeddings_stale_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'cache.db')
            store = EmbeddingStore(db)
            entries = [
                {'source_url': 'https://a',
                 'fields': {'name': 'A', 'one_line': 'x',
                            'recall': 'Use when you need to a.',
                            'tags': 't'}},
                {'source_url': 'https://b',
                 'fields': {'name': 'B', 'one_line': 'y',
                            'recall': 'Use when you need to b.',
                            'tags': 't'}},
            ]
            embed = lambda texts: [[1.0] for _ in texts]     # noqa: E731
            stats = refresh_embeddings(entries, embed, store, 'linking',
                                       'm')
            self.assertEqual(stats, {'embedded': 2, 'skipped_fresh': 0,
                                     'failed': 0})
            stats = refresh_embeddings(entries, embed, store, 'linking',
                                       'm')
            self.assertEqual(stats['skipped_fresh'], 2)
            stats = refresh_embeddings(entries, embed, store, 'linking',
                                       'm', force=True)
            self.assertEqual(stats['embedded'], 2)

            def bad_embed(texts):
                raise EmbeddingError('down')
            entries2 = entries + [
                {'source_url': 'https://c',
                 'fields': {'name': 'C', 'one_line': 'z',
                            'recall': 'Use when you need to c.',
                            'tags': 't'}}]
            stats = refresh_embeddings(entries2, bad_embed, store,
                                       'linking', 'm', force=True)
            self.assertEqual(stats['failed'], 3)   # one batch, all fail
            # the OLD vectors survive a failed refresh
            self.assertEqual(len(store.load('linking', 'm')), 2)
            store.close()


# ---------------------------------------------------------------------------
# 3) Candidates + confirm + the link store
# ---------------------------------------------------------------------------

class TestCandidates(unittest.TestCase):

    def test_find_candidate_pairs(self):
        vectors = {
            'a': [1.0, 0.0],
            'b': [0.99, 0.1],        # near-duplicate of a
            'c': [0.0, 1.0],         # orthogonal
            'd': [0.0, 0.98],        # near-duplicate of c
        }
        pairs = _lk.find_candidate_pairs(vectors, top_k=2, min_score=0.5)
        self.assertTrue(pairs)
        # each unordered pair appears once, sorted by score desc
        seen = set()
        for a, b, score in pairs:
            self.assertLessEqual(a, b)
            self.assertNotIn((a, b), seen)
            seen.add((a, b))
        self.assertIn(('a', 'b'), seen)
        self.assertIn(('c', 'd'), seen)
        self.assertNotIn(('a', 'c'), seen)      # orthogonal < 0.5
        self.assertGreaterEqual(pairs[0][2], pairs[-1][2])

    def test_min_score_prunes_everything(self):
        # near-identical vectors survive a 0.99 floor…
        pairs = _lk.find_candidate_pairs({'a': [1.0], 'b': [1.0]},
                                         min_score=0.99)
        self.assertEqual(len(pairs), 1)
        # …but a 0.6 similarity is pruned at 0.999
        pairs = _lk.find_candidate_pairs({'a': [1.0, 0.1],
                                          'b': [0.6, 0.8]},
                                         min_score=0.999)
        self.assertEqual(pairs, [])


class TestConfirm(unittest.TestCase):

    FIELDS_A = {'name': 'Palette Gen', 'one_line': 'Palettes from images',
                'recall': 'Use when you need to build a scheme.',
                'tags': 'design'}
    FIELDS_B = {'name': 'Contrast Check', 'one_line': 'A11y contrast',
                'recall': 'Use when you need to check contrast.',
                'tags': 'design'}

    def test_prompt_filled(self):
        prompt = _lk.build_confirm_prompt(self.FIELDS_A, self.FIELDS_B)
        self.assertIn('Palette Gen', prompt)
        self.assertIn('Contrast Check', prompt)
        self.assertNotIn('{{', prompt)

    def test_parse_variants(self):
        self.assertEqual(_lk.parse_confirm_reply(
            '{"related": true, "reason": "both color work"}'),
            (True, 'both color work'))
        self.assertEqual(_lk.parse_confirm_reply(
            'Sure! {"related": false, "reason": "no overlap"}'),
            (False, 'no overlap'))
        self.assertEqual(_lk.parse_confirm_reply(
            '{"related": "yes", "reason": "overlap"}'), (True, 'overlap'))
        self.assertEqual(_lk.parse_confirm_reply('garbage'), (None, ''))

    def test_confirm_pair_through_llm(self):
        llm = lambda messages: json.dumps(     # noqa: E731
            {'related': True, 'reason': 'both color tools'})
        related, reason = _lk.confirm_pair(self.FIELDS_A, self.FIELDS_B,
                                           llm)
        self.assertTrue(related)
        self.assertEqual(reason, 'both color tools')


class TestLinkStore(unittest.TestCase):

    def _store(self, tmp):
        return _lk.LinkStore(os.path.join(tmp, 'cache.db'))

    def test_lifecycle_and_never_resuggest(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self.assertEqual(store.suggest('a', 'b', 0.9, 'why'),
                             'pending')
            # same pair again → existing status, no duplicate row
            self.assertEqual(store.suggest('a', 'b', 0.9, 'why'),
                             'pending')
            self.assertEqual(store.counts()['pending'], 1)
            # order-independent identity
            self.assertEqual(store.suggest('b', 'a', 0.9), 'pending')
            self.assertTrue(store.decide('a', 'b', 'approved'))
            self.assertFalse(store.decide('a', 'b', 'approved'))
            # a decided pair is NEVER suggested again
            self.assertEqual(store.suggest('a', 'b', 0.95, 're-ask'),
                             'approved')
            self.assertEqual(store.suggest('b', 'a', 0.95, 're-ask'),
                             'approved')
            # rejected likewise
            store.suggest('c', 'd', 0.8)
            store.decide('c', 'd', 'rejected')
            self.assertEqual(store.suggest('c', 'd', 0.9), 'rejected')
            self.assertEqual(store.counts(),
                             {'pending': 0, 'approved': 1, 'rejected': 1})
            store.close()

    def test_approved_links_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            for i in range(10):
                key = f'https://x/{i:02d}'
                store.suggest('https://center', key, 1.0 - i * 0.01)
                store.decide('https://center', key, 'approved')
            links = store.approved_links()
            self.assertEqual(len(links['https://center']), _lk.LINKS_CAP)
            self.assertEqual(_lk.LINKS_CAP, 7)
            # strongest first
            self.assertEqual(links['https://center'][0]['other'],
                             'https://x/00')
            store.close()


# ---------------------------------------------------------------------------
# 4) The surfaces: Suggestions note + Related blocks + carry-over
# ---------------------------------------------------------------------------

def _mirror_note(source, name='Mirror note', related_block=''):
    body = (f"---\nsource: {source}\nmirror_of: {source}\n"
            f"managed_by: gitcurator\n---\n\n"
            f"> 🔒 Read-only library copy.\n\n# {name}\n\nBody.\n")
    if related_block:
        body += "\n" + related_block + "\n"
    return body


class TestSuggestionsNote(unittest.TestCase):

    def _fixture(self, tmp):
        store = _lk.LinkStore(os.path.join(tmp, 'cache.db'))
        store.suggest('https://a', 'https://b', 0.9, 'both color tools')
        store.suggest('https://c', 'https://d', 0.8, 'similar purpose')
        store.decide('https://c', 'https://d', 'approved')
        store.suggest('https://e', 'https://f', 0.7, 'weak')
        store.decide('https://e', 'https://f', 'rejected')
        fields = {
            'https://a': {'fields': {'name': 'Alpha'}},
            'https://b': {'fields': {'name': 'Beta'}},
            'https://c': {'fields': {'name': 'Gamma'}},
            'https://d': {'fields': {'name': 'Delta'}},
            'https://e': {'fields': {'name': 'Epsilon'}},
            'https://f': {'fields': {'name': 'Zeta'}},
        }
        return store, fields

    def test_note_structure_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, fields = self._fixture(tmp)
            note = _lk.build_suggestions_note(store, fields)
            self.assertIn('Link suggestions', note)
            self.assertIn('- [ ] [[Alpha]] ↔ [[Beta]]', note)
            self.assertIn('`https://a` / `https://b`', note)
            self.assertIn('## Approved', note)
            self.assertIn('## Rejected', note)
            # roundtrip: nothing decided yet
            path = os.path.join(tmp, 'Suggestions.md')
            atomic_write_text(path, note)
            self.assertEqual(_lk.collect_decisions(path), [])
            # tick the pending one → approved
            atomic_write_text(path, note.replace(
                '- [ ] [[Alpha]]', '- [x] [[Alpha]]'))
            decisions = _lk.collect_decisions(path)
            self.assertEqual(decisions, [
                {'key_a': 'https://a', 'key_b': 'https://b',
                 'status': 'approved'}])
            # strike it instead → rejected
            atomic_write_text(path, note.replace(
                '- [ ] [[Alpha]] ↔ [[Beta]]',
                '- [ ] ~~[[Alpha]] ↔ [[Beta]]~~'))
            decisions = _lk.collect_decisions(path)
            self.assertEqual(decisions[0]['status'], 'rejected')
            # free text without URLs is never misparsed as a decision
            ticked_note = note.replace('- [ ] [[Alpha]]', '- [x] [[Alpha]]')
            atomic_write_text(path, ticked_note + '\n- [x] random line\n')
            self.assertEqual(len(_lk.collect_decisions(path)), 1)
            store.close()

    def test_apply_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, fields = self._fixture(tmp)
            result = _lk.apply_decisions(store, [
                {'key_a': 'https://a', 'key_b': 'https://b',
                 'status': 'approved'},
                {'key_a': 'https://zz', 'key_b': 'https://yy',
                 'status': 'approved'},           # unknown pair
            ])
            self.assertEqual(result, {'approved': 1, 'rejected': 0,
                                     'ignored': 1})
            self.assertEqual(store.counts()['approved'], 2)
            store.close()


class TestRelatedBlocks(unittest.TestCase):

    def test_render_insert_extract(self):
        block = _lk.render_related_block(
            [{'other': 'https://x', 'reason': 'same problem',
              'score': 0.9}],
            lambda url: 'Other Note')
        self.assertIn(_lk.RELATED_START, block)
        self.assertIn('[[Other Note]] — same problem', block)
        note = _lk.insert_related_block('# Title\n\nBody\n', block)
        self.assertTrue(_lk.has_related_block(note))
        self.assertEqual(_lk.extract_related_block(note), block)
        # replace in place
        block2 = _lk.render_related_block([], lambda u: '')
        note2 = _lk.insert_related_block(note, block2)
        self.assertEqual(_lk.extract_related_block(note2), block2)
        self.assertEqual(note.count(_lk.RELATED_START), 1)

    def test_apply_only_touches_mirror_copies(self):
        wrote = {}

        def writer(path, content):
            atomic_write_text(path, content)     # real write (recorded)
            wrote[os.path.basename(path)] = content
        with tempfile.TemporaryDirectory() as tmp:
            library = _make_vault(os.path.join(tmp, 'Library'), [
                ('GitHub Projects/Dev Tools/mir.md',
                 _mirror_note('https://github.com/a/a')),
                ('GitHub Projects/Dev Tools/owner.md',
                 '# My own note, no mirror_of\n'),
                ('Websites/Design/web.md',
                 _mirror_note('https://palette.example.com')),
            ])
            approved = {
                'https://github.com/a/a': [
                    {'other': 'https://palette.example.com',
                     'reason': 'both design-adjacent', 'score': 0.9}],
            }
            fields = {
                'https://palette.example.com': {
                    'fields': {'name': 'Palette Gen'}},
            }
            updated = _lk.apply_related_blocks(library, approved, fields,
                                               writer=writer)
            self.assertEqual(sorted(updated), sorted(
                ['GitHub Projects/Dev Tools/mir.md']))
            self.assertIn('[[Palette Gen]] — both design-adjacent',
                          wrote['mir.md'])
            self.assertNotIn('owner.md', wrote)     # unmarked: untouched
            # removing the approval REMOVES the block on the next pass
            updated = _lk.apply_related_blocks(library, {}, fields,
                                               writer=writer)
            self.assertIn('GitHub Projects/Dev Tools/mir.md', updated)
            with open(os.path.join(
                    library, 'GitHub Projects', 'Dev Tools',
                    'mir.md'), encoding='utf-8') as f:
                self.assertFalse(_lk.has_related_block(f.read()))

    def test_preserve_related_block(self):
        block = _lk.render_related_block(
            [{'other': 'https://x', 'reason': 'r', 'score': 0.5}],
            lambda u: 'X')
        old = _lk.insert_related_block('# Old\n\nBody\n', block)
        new = '# New body\n\nRebuilt.\n'
        out = _lk.preserve_related_block(old, new)
        self.assertEqual(_lk.extract_related_block(out), block)
        self.assertTrue(out.startswith('# New body'))
        self.assertEqual(_lk.preserve_related_block('# No block\n',
                                                    '# New\n'), '# New\n')

    def test_mirror_resync_keeps_the_block(self):
        # THE Phase 5 ↔ 6 integration acceptance: an approved Related
        # block survives a full mirror re-apply.
        from gitcurator.core.mirror import run_mirror
        with tempfile.TemporaryDirectory() as tmp:
            machine = _make_vault(os.path.join(tmp, 'machine'), [
                ('Dev Tools/demo.md',
                 _github_note('demo-tool', 'https://github.com/a/demo')),
            ])
            manual = os.path.join(tmp, 'manual')
            os.makedirs(manual)            # the mirror fills, never creates
            run_mirror(manual, machine, None, apply=True)
            mir_path = os.path.join(manual, 'Library',
                                    'GitHub Projects', 'Dev Tools',
                                    'demo.md')
            with open(mir_path, encoding='utf-8') as f:
                first = f.read()
            self.assertFalse(_lk.has_related_block(first))
            block = _lk.render_related_block(
                [{'other': 'https://palette.example.com',
                  'reason': 'design adjacent', 'score': 0.9}],
                lambda u: 'Palette Gen')
            atomic_write_text(mir_path,
                              _lk.insert_related_block(first, block))
            # machine note changes → the mirror rebuilds — the approved
            # block must carry over
            atomic_write_text(
                os.path.join(machine, 'Dev Tools', 'demo.md'),
                _github_note('demo-tool',
                             'https://github.com/a/demo') + '\nEdited.\n')
            run_mirror(manual, machine, None, apply=True)
            with open(mir_path, encoding='utf-8') as f:
                second = f.read()
            self.assertIn('Edited.', second)
            self.assertEqual(_lk.extract_related_block(second), block)
            # and an UNCHANGED note (block already there) is a plain keep
            plan = run_mirror(manual, machine, None, apply=False)
            tree = plan.trees['GitHub Projects']
            self.assertEqual(tree.keeps, 1)
            self.assertEqual(tree.changes, 0)


# ---------------------------------------------------------------------------
# 5) collect_note_fields + the tools end-to-end
# ---------------------------------------------------------------------------

class TestCollectFields(unittest.TestCase):

    def test_both_vaults_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            github = _make_vault(os.path.join(tmp, 'gh'), [
                ('Dev Tools/a.md',
                 _rc.insert_recall_block(
                     _github_note('a', 'https://github.com/a/a'),
                     'Use when you need to a.')),
                ('Dev Tools/unmanaged.md', '# nothing\n'),
            ])
            websites = _make_vault(os.path.join(tmp, 'web'), [
                ('Design/Color Tools/p.md', _website_note()),
                ('Design/Color Tools/p-dup.md', _website_note()),
            ])
            by_key = _lk.collect_note_fields({'github': github,
                                              'websites': websites})
            self.assertEqual(by_key['__duplicate__'], {'count': 1})
            del by_key['__duplicate__']
            self.assertEqual(sorted(by_key),
                             ['https://github.com/a/a',
                              'https://palette.example.com'])
            self.assertEqual(
                by_key['https://palette.example.com']['fields']['name'],
                'Palette Gen')
            self.assertEqual(
                by_key['https://github.com/a/a']['fields']['recall'],
                'Use when you need to a.')


class TestToolsEndToEnd(unittest.TestCase):
    """add_recall_hooks + build_links over synthetic vaults, fake
    embedding + confirm endpoints, tmp everything."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = self.tmp.name
        self.github = _make_vault(os.path.join(root, 'gh'), [
            ('Dev Tools/a.md',
             _github_note('alpha', 'https://github.com/x/alpha',
                          tldr='Alpha generates fixture data.')),
            ('Dev Tools/b.md',
             _github_note('bravo', 'https://github.com/x/bravo',
                          tldr='Bravo generates test fixtures too.',
                          tags='python, testing')),
        ])
        self.websites = _make_vault(os.path.join(root, 'web'), [
            ('Design/Color Tools/pal.md',
             _website_note('Palette Gen',
                           'https://palette.example.com')),
            ('Design/Color Tools/con.md',
             _website_note('Contrast Check',
                           'https://contrast.example.com',
                           best='Use when you need to check contrast.')),
        ])
        self.manual = os.path.join(root, 'manual')
        # the manual vault + Library mirror exist (the suggest step
        # requires it); the mirror only fills an EXISTING manual vault
        os.makedirs(self.manual, exist_ok=True)
        from gitcurator.core.mirror import run_mirror
        run_mirror(self.manual, self.github, self.websites, apply=True)
        self.db = os.path.join(root, 'cache.db')

    def _patch_tool(self, module, config):
        embed_server, embed_root = _start_embed({'mode': 'openai'})
        self.addCleanup(_stop, embed_server)
        real_store = EmbeddingStore
        real_links = _lk.LinkStore

        def fake_llm_call(cfg, args=None):
            def llm(messages):
                # confirm prompt → always related with a reason
                return json.dumps({'related': True,
                                   'reason': 'same problem space'})
            return llm

        def fake_embed_call(cfg, model_override=''):
            def embed(texts):
                # deterministic per-text vectors: text content → simple
                # numeric mapping so alpha≈bravo (fixtures) and
                # pal≈con (color) land near each other
                vectors = []
                for text in texts:
                    seed = sum(ord(c) for c in text[:40]) % 97
                    vectors.append([float(seed % 7), float(seed % 5),
                                    0.5, 0.5])
                return vectors
            label = 'fake · test-embed'
            return embed, label

        patchers = [
            mock.patch.object(module, '_load_config',
                              return_value=config),
            mock.patch.object(module, '_build_llm_call', fake_llm_call),
        ]
        # only the attributes this tool actually imports (build_links
        # has the embedding surface; add_recall_hooks does not)
        for name, replacement in (
                ('build_embedding_call', fake_embed_call),
                ('EmbeddingStore', lambda: real_store(self.db)),
                ('LinkStore', lambda: real_links(self.db))):
            if hasattr(module, name):
                patchers.append(mock.patch.object(module, name,
                                                  replacement))
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        return embed_root

    def test_add_recall_hooks_dryrun_sample_apply(self):
        from gitcurator.tools import add_recall_hooks as tool
        self._patch_tool(tool, {'llm_provider': 'ollama',
                                'vault_path': self.github})
        # dry-run: nothing written
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = tool.main(['--vault', self.github, '--sample', '2'])
        self.assertEqual(rc, 0)
        with open(os.path.join(self.github, 'Dev Tools', 'a.md'),
                  encoding='utf-8') as f:
            self.assertFalse(_rc.has_recall_block(f.read()))
        self.assertIn('DRY RUN', out.getvalue())
        # apply the sample
        with contextlib.redirect_stdout(io.StringIO()):
            rc = tool.main(['--vault', self.github, '--sample', '2',
                            '--apply'])
        self.assertEqual(rc, 0)
        for name in ('a.md', 'b.md'):
            with open(os.path.join(self.github, 'Dev Tools', name),
                      encoding='utf-8') as f:
                content = f.read()
            self.assertTrue(_rc.has_recall_block(content), name)
            self.assertTrue(_rc.get_recall_sentence(content).startswith(
                'Use when you need to'))
        # report file exists
        self.assertTrue(os.path.isfile(os.path.join(
            _rc.report_dir(), 'recall-applied.md')))

    def test_build_links_suggest_tick_collect(self):
        from gitcurator.tools import build_links as tool
        self._patch_tool(tool, {
            'llm_provider': 'ollama', 'vault_path': self.github,
            'website_vault_path': self.websites,
            'manual_vault_path': self.manual})
        # 1) suggest
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = tool.main([])
        self.assertEqual(rc, 0, out.getvalue())
        sugg = _lk.suggestions_path(self.manual)
        self.assertTrue(os.path.isfile(sugg), sugg)
        with open(sugg, encoding='utf-8') as f:
            note = f.read()
        self.assertIn('Link suggestions', note)
        self.assertIn('- [ ] [[', note)
        # 2) the owner ticks the FIRST pending checkbox
        ticked = note.replace('- [ ] [[', '- [x] [[', 1)
        atomic_write_text(sugg, ticked)
        # 3) collect → decisions applied + Related blocks in the mirror
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = tool.main(['--collect'])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn('approved', out.getvalue())
        # some mirror copy now carries a Related (auto) block
        lib = os.path.join(self.manual, 'Library')
        found_block = False
        for r, _d, files in os.walk(lib):
            for fname in files:
                if fname == 'Suggestions.md':
                    continue
                with open(os.path.join(r, fname),
                          encoding='utf-8') as f:
                    if _lk.has_related_block(f.read()):
                        found_block = True
        self.assertTrue(found_block)
        # 4) rejected pairs never reappear: strike the next pending one
        with open(sugg, encoding='utf-8') as f:
            note2 = f.read()
        if '- [ ] [[' in note2:
            struck = note2.replace(
                '- [ ] [[', '- [ ] ~~[[', 1).replace(
                ']] —', ']]~~ —', 1)
            atomic_write_text(sugg, struck)
            with contextlib.redirect_stdout(io.StringIO()):
                rc = tool.main(['--collect'])
            self.assertEqual(rc, 0)
        store = _lk.LinkStore(self.db)
        try:
            counts = store.counts()
            # exactly one box was ticked → exactly one approval
            self.assertEqual(counts['approved'], 1)
            # the struck line (if any pending was left) → at most one
            self.assertIn(counts['rejected'], (0, 1))
        finally:
            store.close()
        # 5) re-suggest: no pair is asked twice (0 new candidates)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = tool.main([])
        self.assertEqual(rc, 0)
        self.assertIn('0 new', out.getvalue())


# ---------------------------------------------------------------------------
# GUI wiring (light — the pattern every phase uses)
# ---------------------------------------------------------------------------

class TestGuiWiring(unittest.TestCase):

    def test_menu_entries_and_methods(self):
        import inspect
        import gitcurator.gui.app as gui_app
        for name in ('run_recall_hooks_dryrun', 'run_link_suggestions',
                     '_run_phase6_tool'):
            self.assertTrue(callable(getattr(gui_app.MainWindow, name)),
                            name)
        src = inspect.getsource(gui_app.MainWindow.initUI)
        self.assertIn('🪝 Recall hooks (dry-run)', src)
        self.assertIn('🔗 Build link suggestions', src)
        # the safe defaults are enforced in the method bodies
        body = inspect.getsource(
            gui_app.MainWindow.run_recall_hooks_dryrun)
        self.assertIn('[]', body)          # argv [] = tool default

    def test_config_keys_exist(self):
        from gitcurator.constants import CONFIG_EXAMPLE as CORE
        from gitcurator.gui.constants import CONFIG_EXAMPLE as GUI
        for cfg in (CORE, GUI):
            self.assertIn('embedding_model', cfg)


if __name__ == '__main__':
    unittest.main()

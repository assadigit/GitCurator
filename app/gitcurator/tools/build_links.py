#!/usr/bin/env python3
"""Build the link suggestions (embeddings → neighbors → LLM confirm).

v0.16.0 — SPEC §6 Phase 6, steps 2–4 and §4.8::

    python gitcurator/tools/build_links.py            # suggest (pending)
    python gitcurator/tools/build_links.py --collect  # apply your ticks

What the suggest step does (writes ONLY the Suggestions note under
``<manual vault>/Library/``):

1. collects the recall fields from both machine vaults (managed notes
   only — name, one-line, the recall sentence, tags; NEVER full text);
2. refreshes the stale embeddings through the configured provider
   (Ollama ``/api/embed``; llama.cpp / any OpenAI-compatible endpoint
   ``/v1/embeddings`` — llama-server needs ``--embeddings``);
3. ranks cosine neighbors (across vaults and within each);
4. asks the LLM once per candidate pair: related? yes/no + a reason
   (prompt ``l01_confirm`` — neutral, no ``about_me.md``);
5. stores NEW suggestions as ``pending`` (a pair is suggested at most
   once; rejected pairs never reappear) and rewrites the Suggestions
   note: tick a checkbox to approve, strike a line through to reject.

The collect step reads your ticks back, updates the store, and writes
the approved "Related (auto)" blocks into the ``Library/`` mirror
copies ONLY (never the machine vaults, never your own notes; the
mirror itself preserves the blocks across re-syncs). Cap: 5–7 links
per note.

Exit codes: 0 ok, 1 nothing to do / paths missing, 2 provider error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# --- make the gitcurator package importable (same bootstrap as the tools)
_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(os.path.dirname(_HERE))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from gitcurator.constants import APP_DIR                      # noqa: E402
from gitcurator.core import llm_client as _llm                # noqa: E402
from gitcurator.core.embeddings import (                      # noqa: E402
    EmbeddingError, EmbeddingStore, build_embedding_call,
    refresh_embeddings)
from gitcurator.core.linking import (                         # noqa: E402
    LINKS_CAP, LinkStore, apply_decisions, apply_related_blocks,
    build_suggestions_note, collect_decisions, collect_note_fields,
    confirm_pair, find_candidate_pairs, suggestions_path)
from gitcurator.core.note_state import VAULT_GITHUB, VAULT_WEBSITES
from gitcurator.core.storage import atomic_write_text

# The embedding store's namespace label. The linking layer pools BOTH
# machine vaults into one vector space ("neighbors across domains and
# within a domain"), so it uses its own label — not 'github'/'websites'.
LINKING_EMBED_VAULT = 'linking'


def _load_config() -> dict:
    cfg_path = os.path.join(APP_DIR, "config.json")
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _build_llm_call(config, args):
    """Same router as add_recall_hooks (chat confirmation calls)."""
    provider = (args.provider or config.get('llm_provider', 'ollama'))
    num_ctx = int(config.get('llm_num_ctx', _llm.DEFAULT_NUM_CTX) or 0) \
        or None

    if provider == 'llamacpp':
        base = (config.get('llamacpp_api_url', '')
                or _llm.LLAMACPP_DEFAULT_BASE + '/v1')
        api_url = _llm.normalize_llamacpp_api_url(base)
        api_key = config.get('llamacpp_api_key', '')
        model = config.get('llamacpp_model', '')
        if not model:
            probe = _llm.probe_llamacpp(api_url, api_key)
            if not probe.get('found'):
                probe = _llm.detect_llamacpp(api_key) or probe
            if probe.get('found'):
                api_url = probe['base_url'] + '/v1'
                model = probe.get('model') or ''
        if not model:
            raise SystemExit(
                "llama.cpp provider needs a running llama-server or "
                "config.json llamacpp_api_url/llamacpp_model")
        timeout = float(config.get('llm_timeout_s', 300) or 300)

        def llm(messages):
            return _llm.openai_chat(api_url, api_key, model, messages,
                                    timeout, json_mode=True,
                                    num_ctx=num_ctx)
        print(f"  LLM: {model} via llama.cpp {api_url}")
        return llm

    if provider == 'cloud':
        api_url = config.get('cloud_api_url', '')
        api_key = config.get('cloud_api_key', '')
        model = config.get('cloud_model', '')
        if not api_url or not model:
            raise SystemExit("cloud provider needs config.json "
                             "cloud_api_url + cloud_model")
        timeout = float(config.get('llm_timeout_s', 300) or 300)

        def llm(messages):
            return _llm.openai_chat(api_url, api_key, model, messages,
                                    timeout, json_mode=True,
                                    num_ctx=num_ctx)
        print(f"  LLM: {model} via {api_url}")
        return llm

    import ollama
    oll = config.get('ollama') or {}
    host = (oll.get('base_url') if isinstance(oll, dict) else None) \
        or 'http://127.0.0.1:11434'
    model = (args.model or oll.get('model') or 'llama3')
    timeout = float(config.get('llm_timeout_s', 300) or 300)
    client = ollama.Client(host=host)

    def llm(messages):
        return _llm.ollama_chat(client, model, messages, timeout,
                                json_mode=True, num_ctx=num_ctx)
    print(f"  LLM: {model} via Ollama ({host})")
    return llm


def _suggest(args, config) -> int:
    manual = (args.manual_vault or config.get('manual_vault_path', '')
              or '').strip()
    if not manual or not os.path.isdir(manual):
        print("Manual Notes vault not set or not found (config "
              "manual_vault_path or --manual-vault) — the Suggestions "
              "note lives in its Library/.", file=sys.stderr)
        return 1
    library = os.path.join(manual, 'Library')
    if not os.path.isdir(library):
        print("Library/ not found in the manual vault — run "
              "tools/mirror_manual.py --apply first (the Related blocks "
              "live in the mirror copies).", file=sys.stderr)
        return 1

    vaults = {VAULT_GITHUB: (args.github_vault
                             or config.get('vault_path', '')),
              VAULT_WEBSITES: (args.websites_vault
                               or config.get('website_vault_path', ''))}
    vaults = {k: v for k, v in vaults.items() if v}
    print("Collecting recall fields "
          + " · ".join(f"{k}: {v}" for k, v in vaults.items()))
    by_key = collect_note_fields(vaults)
    dup_marker = by_key.pop('__duplicate__', None)
    if dup_marker:
        print(f"  (skipped {dup_marker.get('count', 0)} duplicate-source "
              "note(s))")
    if not by_key:
        print("No managed notes found — nothing to link.")
        return 1
    print(f"  {len(by_key)} managed note(s)")

    # embeddings (stale only)
    embed, label = build_embedding_call(config, args.embed_model)
    print(f"  Embeddings: {label}")
    store = EmbeddingStore()
    link_store = LinkStore()
    try:
        model_key = label.split('·')[-1].strip() or 'default'
        entries = [{'source_url': key, 'fields': item['fields']}
                   for key, item in by_key.items()]
        stats = refresh_embeddings(entries, embed, store,
                                   LINKING_EMBED_VAULT, model_key,
                                   progress=lambda m: print(m))
        print(f"  embeddings: {stats['embedded']} embedded, "
              f"{stats['skipped_fresh']} fresh, {stats['failed']} failed")

        vectors = {url: vec for url, (vec, _h)
                   in store.load(LINKING_EMBED_VAULT, model_key).items()}
        if len(vectors) < 2:
            print("Fewer than 2 embeddings — nothing to compare (is the "
                  "embedding model pulled? e.g. ollama pull "
                  "nomic-embed-text).", file=sys.stderr)
            return 1

        # candidates
        candidates = find_candidate_pairs(
            vectors, top_k=args.top_k, min_score=args.min_score)
        known = link_store.known_pairs()
        fresh = [(a, b, s) for a, b, s in candidates
                 if tuple(sorted((a, b))) not in known]
        print(f"  candidates: {len(candidates)} above "
              f"{args.min_score:.2f} — {len(fresh)} new "
              f"({len(candidates) - len(fresh)} already suggested)")

        # LLM confirmation — one call per new candidate
        llm = _build_llm_call(config, args)
        confirmed = 0
        asked = 0
        for key_a, key_b, score in fresh:
            if asked >= args.max_confirm:
                print(f"  … stopping at --max-confirm {args.max_confirm} "
                      "for this run (re-run to continue)")
                break
            fields_a = (by_key.get(key_a) or {}).get('fields') or {}
            fields_b = (by_key.get(key_b) or {}).get('fields') or {}
            asked += 1
            related, reason = confirm_pair(fields_a, fields_b, llm)
            if related is True:
                link_store.suggest(key_a, key_b, score, reason)
                confirmed += 1
                print(f"  + related: {fields_a.get('name', key_a)[:40]} ↔ "
                      f"{fields_b.get('name', key_b)[:40]} — {reason}")
            elif related is False:
                # a NO is recorded as rejected — it must never be asked
                # again (the store's never-resuggest rule)
                link_store.suggest(key_a, key_b, score,
                                   'LLM: ' + (reason or 'not related'))
                link_store.decide(key_a, key_b, 'rejected')
            else:
                print(f"  ? unparseable answer for {key_a} ↔ {key_b} — "
                      "skipped (will retry next run)")

        # the Suggestions note (the ONLY write of the suggest step)
        note = build_suggestions_note(link_store, by_key)
        out = suggestions_path(manual)
        atomic_write_text(out, note)
        counts = link_store.counts()
        print(f"\nSuggestions note: {out}")
        print(f"  pending {counts['pending']} · approved "
              f"{counts['approved']} · rejected {counts['rejected']} "
              f"(cap {LINKS_CAP} links/note)")
        print("Tick a checkbox to approve, strike a line through to "
              "reject, then run with --collect.")
        return 0
    finally:
        store.close()
        link_store.close()


def _collect(args, config) -> int:
    manual = (args.manual_vault or config.get('manual_vault_path', '')
              or '').strip()
    if not manual or not os.path.isdir(manual):
        print("Manual Notes vault not set or not found.", file=sys.stderr)
        return 1
    path = suggestions_path(manual)
    if not os.path.isfile(path):
        print(f"No Suggestions note yet ({path}) — run the suggest step "
              "first.", file=sys.stderr)
        return 1

    decisions = collect_decisions(path)
    if not decisions:
        print("No decisions found — tick checkboxes (approve) or strike "
              "lines through (reject) in the Suggestions note first.")
        return 0
    link_store = LinkStore()
    try:
        result = apply_decisions(link_store, decisions)
        print(f"Decisions applied: {result['approved']} approved, "
              f"{result['rejected']} rejected, "
              f"{result['ignored']} ignored (already decided).")

        # write the Related (auto) blocks into the MIRROR copies only
        vaults = {VAULT_GITHUB: (args.github_vault
                                 or config.get('vault_path', '')),
                  VAULT_WEBSITES: (args.websites_vault
                                   or config.get(
                                       'website_vault_path', ''))}
        vaults = {k: v for k, v in vaults.items() if v}
        by_key = collect_note_fields(vaults)
        by_key.pop('__duplicate__', None)
        library = os.path.join(manual, 'Library')
        approved = link_store.approved_links()
        updated = apply_related_blocks(library, approved, by_key)
        print(f"Related (auto) blocks: {len(updated)} mirror note(s) "
              f"updated under {library}")
        # regenerate the Suggestions note (decisions moved out of pending)
        note = build_suggestions_note(link_store, by_key)
        atomic_write_text(path, note)
        print("Suggestions note refreshed.")
        return 0
    finally:
        link_store.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Link suggestions: embeddings → neighbors → LLM "
                    "confirm → the Suggestions note (--collect applies "
                    "your ticks)")
    parser.add_argument('--collect', action='store_true',
                        help='apply the decisions you made in the '
                             'Suggestions note (ticks = approve, strikes '
                             '= reject) and write the Related blocks')
    parser.add_argument('--manual-vault', default='',
                        help='Manual Notes vault (default: config)')
    parser.add_argument('--github-vault', default='',
                        help='GitHub machine vault (default: config)')
    parser.add_argument('--websites-vault', default='',
                        help='Websites machine vault (default: config)')
    parser.add_argument('--embed-model', default='',
                        help='embedding model (default: config '
                             'embedding_model, else the provider default)')
    parser.add_argument('--top-k', type=int, default=8,
                        help='candidates per note (default 8)')
    parser.add_argument('--min-score', type=float, default=0.30,
                        help='cosine floor (default 0.30)')
    parser.add_argument('--max-confirm', type=int, default=50,
                        help='LLM confirmations per run (default 50 — '
                             'the first batch waits for the owner anyway)')
    parser.add_argument('--provider', default='',
                        help='ollama | llamacpp | cloud (default: config)')
    parser.add_argument('--model', default='',
                        help='Ollama chat model override (default: config)')
    args = parser.parse_args(argv)

    config = _load_config()
    try:
        if args.collect:
            return _collect(args, config)
        return _suggest(args, config)
    except EmbeddingError as exc:
        print(f"Embedding error: {exc}", file=sys.stderr)
        print("  (llama-server needs --embeddings for /v1/embeddings; "
              "Ollama needs the model pulled, e.g. "
              "'ollama pull nomic-embed-text')", file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

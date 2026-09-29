#!/usr/bin/env python3
"""Add recall hooks ("Best used for") to the existing GitHub notes.

v0.16.0 — SPEC §6 Phase 6, step 1. Every GitHub note gets ONE
delimited, fingerprint-invisible block with a NEUTRAL recall sentence
(prompt ``r01_recall`` — ``about_me.md`` is NOT used; only the GitHub
repo prompts personalize). Nothing else in the note changes.

DRY-RUN BY DEFAULT (the diff is printed and reported). The owner
approves a sample before any bulk run (SPEC)::

    # 1) see 20 samples — nothing written
    python gitcurator/tools/add_recall_hooks.py --sample 20

    # 2) approve them (writes only those 20)
    python gitcurator/tools/add_recall_hooks.py --sample 20 --apply

    # 3) the rest, once the samples read well
    python gitcurator/tools/add_recall_hooks.py --apply

The LLM provider follows config.json (ollama / llama.cpp — detected
automatically like everywhere else — / any OpenAI-compatible endpoint);
``--provider`` overrides. Vault from ``--vault`` or config
``vault_path``. Exit codes: 0 ok, 1 nothing to do / vault missing,
2 provider error.
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
from gitcurator.core.recall import (                          # noqa: E402
    plan_recall_hooks, recall_report, report_dir, run_recall_hooks)


def _load_config() -> dict:
    cfg_path = os.path.join(APP_DIR, "config.json")
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _build_llm_call(config, args):
    """The same router as the other tools: the DETECTED llama.cpp server,
    an OpenAI-compatible endpoint, or local Ollama — through the shared
    llm_client helpers. Task tag 'analyze' (the note-writing pass)."""
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
                "llama.cpp provider needs a running llama-server (its "
                "model is detected automatically) or config.json "
                "llamacpp_api_url/llamacpp_model")
        timeout = float(config.get('llm_timeout_s', 300) or 300)
        print(f"  LLM: {model} via llama.cpp {api_url}"
              + (f" (num_ctx={num_ctx})" if num_ctx else ""))

        def llm(messages, task=None):
            return _llm.openai_chat(api_url, api_key, model, messages,
                                    timeout, json_mode=True,
                                    num_ctx=num_ctx)
        return llm

    if provider == 'cloud':
        api_url = config.get('cloud_api_url', '')
        api_key = config.get('cloud_api_key', '')
        model = config.get('cloud_model', '')
        if not api_url or not model:
            raise SystemExit(
                "cloud provider needs config.json cloud_api_url + "
                "cloud_model")
        timeout = float(config.get('llm_timeout_s', 300) or 300)
        print(f"  LLM: {model} via {api_url}"
              + (f" (num_ctx={num_ctx})" if num_ctx else ""))

        def llm(messages, task=None):
            return _llm.openai_chat(api_url, api_key, model, messages,
                                    timeout, json_mode=True,
                                    num_ctx=num_ctx)
        return llm

    import ollama
    oll = config.get('ollama') or {}
    host = (oll.get('base_url') if isinstance(oll, dict) else None) \
        or 'http://127.0.0.1:11434'
    model = (args.model or oll.get('model') or 'llama3')
    timeout = float(config.get('llm_timeout_s', 300) or 300)
    client = ollama.Client(host=host)
    print(f"  LLM: {model} via Ollama ({host})"
          + (f" (num_ctx={num_ctx})" if num_ctx else ""))

    def llm(messages, task=None):
        return _llm.ollama_chat(client, model, messages, timeout,
                                json_mode=True, num_ctx=num_ctx)
    return llm


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Add recall hooks to the GitHub notes (dry-run by "
                    "default)")
    parser.add_argument('--vault', default='',
                        help='GitHub vault path (default: config vault_path)')
    parser.add_argument('--sample', type=int, default=0, metavar='N',
                        help='process only the first N planned notes '
                             '(the owner approves 20 before any bulk run)')
    parser.add_argument('--force', action='store_true',
                        help='refresh notes that already carry a hook')
    parser.add_argument('--apply', action='store_true',
                        help='write the blocks (default: dry-run)')
    parser.add_argument('--provider', default='',
                        help='ollama | llamacpp | cloud (default: config)')
    parser.add_argument('--model', default='',
                        help='Ollama model override (default: config)')
    args = parser.parse_args(argv)

    config = _load_config()
    vault = args.vault or config.get('vault_path', '')
    if not vault or not os.path.isdir(vault):
        print("GitHub vault not set or not found (config vault_path or "
              "--vault).", file=sys.stderr)
        return 1

    plan = plan_recall_hooks(vault, force=args.force)
    total = len(plan)
    limit = args.sample if args.sample and args.sample > 0 else None
    todo = plan[:limit] if limit else plan
    print(f"GitHub vault: {vault}")
    print(f"Notes needing a hook: {total}"
          + (f" — running the first {len(todo)} (--sample)"
             if limit and len(todo) < total else ""))
    if not todo:
        print("Nothing to do.")
        return 0

    llm = _build_llm_call(config, args)
    mode = "APPLY" if args.apply else "DRY RUN (nothing written)"
    print(f"Mode: {mode}")
    results = run_recall_hooks(todo, llm, apply=args.apply,
                               progress=lambda msg: print(msg))

    report = recall_report(results, vault, applied=args.apply)
    out_path = os.path.join(report_dir(),
                            'recall-' + ('applied' if args.apply
                                         else 'dryrun')
                            + '.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\nReport: {out_path}")
    if args.apply:
        written = sum(1 for r in results if r.get('written'))
        print(f"Written: {written} note(s); "
              f"{sum(1 for r in results if r.get('error'))} error(s).")
        print("The blocks are fingerprint-invisible — note_state will "
              "not read them as edits.")
    else:
        print("Dry run — re-run with --apply to write "
              f"{len(results)} hook(s).")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

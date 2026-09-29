#!/usr/bin/env python3
"""
run_golden_websites.py — golden-set runner for the Websites pipeline
(Phase 2, SPEC §6).

Two modes:

  --offline   A fake LLM answers from the golden file itself. Exercises the
              whole machinery — prompt loading, slot filling, taxonomy
              validation, note building, report rendering — with zero
              network. This is the mode CI runs.

  --live      Real fetches + the real LLM configured in app/config.json
              (cloud_api_url/cloud_api_key/cloud_model) or
              --api-url/--api-key/--model overrides. Produces the
              side-by-side expected-vs-actual report the owner reads.

Both modes write notes into a THROWAWAY temp vault and a throwaway cache —
a golden run never touches a real vault or the real cache.db (SPEC
non-negotiable #1).

Output: a Markdown report (default app/reports/golden/websites-report-<mode>.md,
--out overrides). Exit code 0 when every model answer was a VALID taxonomy
name (SPEC acceptance: "100% valid category names"); 1 otherwise.

Usage:
    python gitcurator/tools/run_golden_websites.py --offline
    python gitcurator/tools/run_golden_websites.py --live --out docs/reports/golden-websites-report.md
"""

import argparse
import json
import os
import sys
import tempfile
import shutil
from datetime import datetime

# Make the app importable from the tools folder
# (…/app/gitcurator/tools -> …/app).
_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(os.path.dirname(_HERE))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from gitcurator.core import website_pipeline as wp   # noqa: E402
from gitcurator.core.taxonomy import load_taxonomy_from_config  # noqa: E402
from gitcurator.core.links import normalize_website_url  # noqa: E402

GOLDEN_PATH = os.path.join(_APP, "tests", "golden", "websites.json")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def load_golden(path=GOLDEN_PATH):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    entries = data.get('entries') or []
    if not entries:
        raise SystemExit(f"golden set {path} has no entries")
    return data, entries


def offline_fetch(entries):
    """Deterministic offline fetch: a canned full page per golden entry.
    Offline mode must touch NO network — CI runs it on every push."""
    by_url = {normalize_website_url(e['url']): e for e in entries}

    def fetch(url, **kwargs):
        from gitcurator.core import web_fetch
        entry = by_url.get(normalize_website_url(url)) or {}
        title = (entry.get('notes') or 'Golden test site').capitalize()
        desc = entry.get('notes') or 'Offline golden-run fixture page.'
        html = (f"<!doctype html><html><head><title>{title}</title>"
                f"<meta name=\"description\" content=\"{desc}\">"
                "</head><body><p>Offline golden-run fixture page for the "
                "Websites pipeline. This body text stands in for the real "
                "page so the pipeline runs end to end without any network "
                "access at all.</p></body></html>")
        return web_fetch.FetchResult(
            url=url, final_url=url, status='full', reason='',
            http_status=200, content_type='text/html; charset=utf-8',
            charset='utf-8', body=html.encode('utf-8'), text=html,
            elapsed_s=0.0)
    return fetch


def offline_llm(entries):
    """Fake LLM keyed by canonical URL: answers w01/w02 from the golden
    file, w03 with a canned (valid) analysis."""
    by_url = {normalize_website_url(e['url']): e for e in entries}

    def llm(messages, task=None):
        text = messages[0]['content'] if messages else ''
        # Find which URL this prompt is about (the URL line is stable).
        url = ''
        for line in text.splitlines():
            if line.startswith('Website: '):
                url = line[len('Website: '):].strip()
                break
        entry = by_url.get(normalize_website_url(url)) or {}
        if 'filing a website into a personal library' in text:
            return json.dumps({
                'category': entry.get('expected_category', 'Design'),
                'confidence': 'high', 'reason': 'golden offline mode'})
        if 'was filed under' in text:
            sub = entry.get('expected_subcategory') or 'none'
            return json.dumps({'subcategory': sub, 'confidence': 'high'})
        return json.dumps({
            'name': entry.get('notes') or 'Golden test site',
            'one_line': 'Offline golden-run placeholder description.',
            'core_offerings': ['Offline validation', 'Report generation',
                               'No network'],
            'standout_feature': '', 'best_used_for':
            'Use when you need to validate the pipeline without network.',
            'pricing': 'unknown', 'login_required': 'unknown',
            'similar_tools': [], 'tags': ['golden'],
            'confidence': 'high'})
    return llm


def live_llm(api_url, api_key, model, timeout_s, num_ctx=None):
    """One real call per prompt through llm_client.openai_chat — the
    SAME hardened path the app uses (wall-clock timeout wrapper,
    response_format JSON mode with a clean memoized fallback when the
    server rejects it, clear errors on malformed bodies). Retries twice
    on transient server errors (a live run hammers the endpoint;
    429/500 windows pass)."""
    import time as _time
    from gitcurator.core import llm_client as _llm

    def llm(messages, task=None):
        last_err = None
        for attempt in range(3):
            try:
                return _llm.openai_chat(
                    api_url, api_key, model, messages, timeout_s,
                    json_mode=True, temperature=0.2, num_ctx=num_ctx)
            except _llm.CloudLLMHTTPError as e:
                last_err = e
                if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    _time.sleep(45)
                    continue
                raise
        raise last_err
    return llm


def live_llm_ollama(host, model, timeout_s, num_ctx=None):
    """v0.13.0 — Phase 4: the golden set on a local Ollama server, through
    llm_client.ollama_chat (explicit options.num_ctx + the shared timeout
    wrapper). Retries twice on transient server errors (5xx/429) exactly
    like the openai backend — parity found the hard way in the v0.13.0
    backends comparison run, where one upstream hiccup sent four links to
    _review that the openai path would have retried. Raises with a clear
    message when the server is down."""
    import re
    import time as _time
    import ollama as _ol
    from gitcurator.core import llm_client as _llm
    client = _ol.Client(host=host)
    _transient = re.compile(r'(?:status code|HTTP Error)\s*[:=]?\s*(429|50[0234])',
                            re.IGNORECASE)

    def llm(messages, task=None):
        last_err = None
        for attempt in range(3):
            try:
                return _llm.ollama_chat(
                    client, model, messages, timeout_s,
                    json_mode=True, num_ctx=num_ctx)
            except Exception as e:
                last_err = e
                if _transient.search(str(e)) and attempt < 2:
                    _time.sleep(45)
                    continue
                raise
        raise last_err
    return llm


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def verdict_of(result, expected_category, expected_subcategory, taxonomy):
    """✅ exact · ◐ category right, subcategory differs · ⚠️ review/failed ·
    ❌ mismatch. An INVALID actual category (not in the taxonomy) can never
    appear — the pipeline validates answers — but the report still checks."""
    if result.get('outcome') != 'processed':
        return '⚠️ ' + (result.get('error') or result.get('outcome', '?'))
    actual_cat = result.get('category') or ''
    if not taxonomy.is_category(actual_cat):
        return '❌ INVALID CATEGORY: ' + actual_cat     # should be impossible
    if actual_cat != expected_category:
        return f'❌ expected {expected_category}'
    exp_sub = expected_subcategory or ''
    act_sub = result.get('subcategory') or ''
    if exp_sub == act_sub:
        return '✅ exact'
    if not exp_sub or not act_sub:
        return f'◐ subcategory: expected {exp_sub or "(none)"}'
    return f'◐ subcategory: expected {exp_sub}'


def render_report(mode, entries, results, taxonomy, notes=''):
    lines = []
    lines.append('# Golden-set report — Websites pipeline'
                 f' ({mode} mode)')
    lines.append('')
    lines.append(f'*Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}'
                 f' · {len(entries)} links · taxonomy:'
                 f' {taxonomy.summary()}*')
    lines.append('')
    lines.append('> A golden run writes to a throwaway vault and a throwaway'
                 ' cache — never to the real ones.')
    lines.append('')
    counts = {'exact': 0, 'category_only': 0, 'mismatch': 0, 'review': 0}
    for e, r in zip(entries, results):
        v = verdict_of(r, e.get('expected_category'),
                       e.get('expected_subcategory'), taxonomy)
        if v.startswith('✅'):
            counts['exact'] += 1
        elif v.startswith('◐'):
            counts['category_only'] += 1
        elif v.startswith('⚠️'):
            counts['review'] += 1
        else:
            counts['mismatch'] += 1

    lines.append('## Summary')
    lines.append('')
    lines.append('| Verdict | Count |')
    lines.append('|---------|-------|')
    lines.append(f"| ✅ exact (category + subcategory) | {counts['exact']} |")
    lines.append(f"| ◐ category match, subcategory differs | {counts['category_only']} |")
    lines.append(f"| ❌ category mismatch | {counts['mismatch']} |")
    lines.append(f"| ⚠️ needs review / failed | {counts['review']} |")
    lines.append('')
    invalid = sum(1 for r in results
                  if r.get('outcome') == 'processed'
                  and not taxonomy.is_category(r.get('category') or ''))
    lines.append(f'**Model answers that were invalid taxonomy names: '
                 f'{invalid}** (the pipeline validates every answer and '
                 'falls back to _review, so this must be 0.)')
    lines.append('')
    lines.append('## Side by side')
    lines.append('')
    lines.append('| # | Link | Fetched | Expected | Actual | Verdict |')
    lines.append('|---|------|---------|----------|--------|---------|')
    for i, (e, r) in enumerate(zip(entries, results), 1):
        exp = e.get('expected_category') or '—'
        if e.get('expected_subcategory'):
            exp += f" / {e['expected_subcategory']}"
        act = r.get('category') or '—'
        if r.get('subcategory'):
            act += f" / {r['subcategory']}"
        v = verdict_of(r, e.get('expected_category'),
                       e.get('expected_subcategory'), taxonomy)
        url = e.get('url', '')
        lines.append(f"| {i} | [{url}]({url}) | {r.get('fetch_status', '')} "
                     f"| {exp} | {act} | {v} |")
    lines.append('')
    if notes:
        lines.append('## Notes')
        lines.append('')
        lines.append(notes)
        lines.append('')
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Golden-set runner for the Websites pipeline '
                    '(--offline or --live)')
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--offline', action='store_true',
                      help='fake LLM from the golden file (no network)')
    mode.add_argument('--live', action='store_true',
                      help='real fetches + the configured LLM')
    ap.add_argument('--out', default='',
                    help='report path (default app/reports/golden/…)')
    ap.add_argument('--api-url', default='', help='live: OpenAI-compatible'
                    ' base URL (overrides config; for --backend ollama this'
                    ' is the Ollama host)')
    ap.add_argument('--api-key', default='', help='live: bearer token'
                    ' (overrides config; never printed)')
    ap.add_argument('--model', default='', help='live: model name')
    ap.add_argument('--backend', choices=['openai', 'ollama'],
                    default='openai',
                    help='live: backend to run against (default openai ='
                    ' any OpenAI-compatible endpoint: llama.cpp, vLLM, LM'
                    ' Studio, cloud; ollama = local Ollama server)')
    ap.add_argument('--num-ctx', type=int, default=0,
                    help='live: explicit context window in tokens (0 ='
                    ' config llm_num_ctx / the server default)')
    ap.add_argument('--timeout', type=float, default=60,
                    help='live: per-LLM-call timeout (s)')
    args = ap.parse_args(argv)

    data, entries = load_golden()
    taxonomy = load_taxonomy_from_config({})

    # Throwaway vault + cache — a golden run never touches real ones.
    tmp = tempfile.mkdtemp(prefix='golden-websites-')
    vault = os.path.join(tmp, 'websites')
    db = wp.WebsiteStateDB(db_path=os.path.join(tmp, 'cache.db'))

    if args.offline:
        llm = offline_llm(entries)
        notes = ('Offline mode: the "actual" answers COME FROM the golden '
                 'file via a fake LLM — this validates the machinery '
                 '(prompts, validation, note building), not the model. '
                 'Run --live for the real comparison.')
    else:
        cfg = {}
        cfg_path = os.path.join(_APP, 'config.json')
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
            except (OSError, json.JSONDecodeError):
                cfg = {}
        api_url = args.api_url or cfg.get('cloud_api_url', '')
        api_key = args.api_key or cfg.get('cloud_api_key', '')
        num_ctx = (args.num_ctx or int(cfg.get(
            'llm_num_ctx', 0) or 0)) or None
        if args.backend == 'ollama':
            host = args.api_url or (cfg.get('ollama') or {}).get(
                'base_url', 'http://127.0.0.1:11434')
            model = args.model or (cfg.get('ollama') or {}).get(
                'model', '')
            if not model:
                print('live ollama mode needs --model (or a config.json '
                      'with ollama.model)', file=sys.stderr)
                return 2
            try:
                import ollama as _ol
                from gitcurator.core import llm_client as _llm
                _names = _llm.list_models_with_timeout(
                    _ol.Client(host=host), 15)
            except Exception as e:
                print(f'Ollama pre-flight failed ({host}): {e}',
                      file=sys.stderr)
                return 2
            if _names and model not in _names:
                print(f"⚠️ model '{model}' not in the Ollama list "
                      f"({', '.join(_names[:5])}…) — trying anyway",
                      file=sys.stderr)
            llm = live_llm_ollama(host, model, args.timeout,
                                  num_ctx=num_ctx)
            notes = (f'Live mode (Ollama): real fetches + `{model}` at '
                     f'`{host}`'
                     + (f' (num_ctx={num_ctx})' if num_ctx else '')
                     + '. Expected values are the agent\'s proposal — '
                       'the owner approves or edits them in '
                       'tests/golden/websites.json.')
        else:
            model = args.model or cfg.get('cloud_model', '')
            if not api_url or not model:
                print('live mode needs --api-url and --model (or a '
                      'config.json with cloud_api_url/cloud_model)',
                      file=sys.stderr)
                return 2
            # /v1/models pre-flight — warn-never-block (llama.cpp builds
            # and proxies may legitimately hide the route).
            from gitcurator.core import llm_client as _llm
            try:
                _names = _llm.openai_list_models(api_url, api_key, 15)
                if _names and model.lower() not in {n.lower()
                                                    for n in _names}:
                    print(f"⚠️ '{model}' is not in the /models list "
                          f"({len(_names)} listed) — trying anyway",
                          file=sys.stderr)
            except _llm.CloudLLMError as e:
                print(f'ℹ️ /models pre-flight unavailable: {e}',
                      file=sys.stderr)
            llm = live_llm(api_url, api_key, model, args.timeout,
                           num_ctx=num_ctx)
            notes = (f'Live mode ({args.backend}): real fetches + '
                     f'`{model}` via `{api_url}`'
                     + (f' (llm_num_ctx={num_ctx})' if num_ctx else '')
                     + '. Expected values are the agent\'s proposal —'
                       ' the owner approves or edits them in'
                       ' tests/golden/websites.json.')

    config = {'website_vault_path': vault,
              'web_fetch_timeout_s': 20, 'web_domain_delay_s': 1.0}
    processed = set()
    pipeline = wp.WebsitePipeline(
        config=config, llm_call=llm,
        vault_index_has=lambda u: u in processed,
        state=db,
        fetch_fn=offline_fetch(entries) if args.offline else None,
        log=lambda m, l='info': print(f'  [{l}] {m}'))

    try:
        results = pipeline.run([e['url'] for e in entries])
        for r in results:
            if r.get('outcome') == 'processed':
                processed.add(r.get('canonical'))
    finally:
        db.close()

    report = render_report(
        'offline' if args.offline else f'live-{args.backend}',
        entries, results, taxonomy, notes=notes)

    if args.out:
        out_path = args.out
    else:
        out_dir = os.path.join(_APP, 'reports', 'golden')
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, 'websites-report-'
                                + ('offline' if args.offline
                                   else f'live-{args.backend}')
                                + '.md')
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(report)

    invalid = sum(1 for r in results
                  if r.get('outcome') == 'processed'
                  and not taxonomy.is_category(r.get('category') or ''))
    print(f'\nReport: {out_path}')
    print(f'Invalid category names: {invalid} (must be 0)')
    ok = sum(1 for r in results if r.get('outcome') == 'processed')
    print(f'Processed: {ok}/{len(entries)}')
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if invalid == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())

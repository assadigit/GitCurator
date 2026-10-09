"""tests/test_channelua.py — v0.60.2 THE 1010 LAW (the channel's own name).

The owner's report chain (v0.60.0 → v0.60.1): the banish-gate ask never
reached Telegram. A REAL run against the live worker (session
verification — the pairing inserted into D1 in the exact /pair result
shape) found the second stacked cause: every CloudflareSync request
rode urllib's default signature ``Python-urllib/3.x``, and Cloudflare's
edge answers THAT signature with **error 1010 — "banned browser
signature"** — a 403 that dies before the Worker's own code ever runs
(no HMAC check, no route). Replayed byte-identically under any other
name (``GitCurator/…``, curl, a browser UA) the request passes; under
the banned one it never did. So the channel now introduces itself on
every leg — pair, the HMAC API, health — as the app it is.

These tests are hermetic (zero network): the opener is scripted, the
requests are captured, the LAW is that every captured request wears
the app's name and never urllib's default. Pure stdlib + unittest.mock
(the fifth-door precedent).
"""

import io
import json
import unittest
import urllib.error
from unittest import mock

from gitcurator.cloud import cloudflare_sync as cs
from gitcurator.cloud.cloudflare_sync import CloudflareSync

#: The paired-config shape the desktop stores after /pair succeeds.
_PAIRED = {
    'cloudflare_worker_url': 'https://bot.example.workers.dev',
    'cloudflare_install_id': '11111111-2222-3333-4444-555555555555',
    'cloudflare_shared_secret': 'a' * 64,
    'cloudflare_enabled': True,
}


class _ScriptedResponse:
    """The minimal ``opener.open`` result the sync reads."""

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
    """Captures every Request; answers from a scripted response list
    (HTTP errors raise like the real opener)."""

    def __init__(self, responses):
        self.requests = []
        self._responses = list(responses)

    def open(self, req, timeout=None):
        self.requests.append(req)
        if not self._responses:
            raise AssertionError('a request the script did not expect')
        status, body = self._responses.pop(0)
        if status >= 400:
            raise urllib.error.HTTPError(
                req.full_url, status, 'scripted', {},
                io.BytesIO(body))
        return _ScriptedResponse(status, body)


def _capture(responses):
    """Patch build_opener inside cloudflare_sync's urllib with the
    scripted capture; returns the opener (whose .requests hold the
    Request objects after the calls under test)."""
    opener = _ScriptedOpener(responses)
    return opener, mock.patch.object(
        cs.urllib.request, 'build_opener', return_value=opener)


def _ua(req):
    """The User-Agent a captured Request wears (urllib stores the key
    capitalized ``User-agent``)."""
    return (req.headers or {}).get('User-agent') or ''


class TestTheConstant(unittest.TestCase):
    """The name itself."""

    def test_the_name_is_the_app_not_urllib(self):
        # the banned signature is exactly what the constant must not be:
        self.assertFalse(cs.HTTP_USER_AGENT.startswith('Python-urllib'))
        self.assertIn('GitCurator', cs.HTTP_USER_AGENT)

    def test_the_name_is_versioned(self):
        # the string stays in sync with the release (the comment law):
        self.assertRegex(cs.HTTP_USER_AGENT, r'GitCurator/\d+\.\d+\.\d+')


class TestHmacRequestsWearTheName(unittest.TestCase):
    """The HMAC API legs (every /api/* call — pending, mirror, banish)."""

    def test_get_pending_introduces_the_app(self):
        sync = CloudflareSync(dict(_PAIRED))
        opener, patch = _capture([
            (200, json.dumps({'success': True, 'pending': [],
                              'count': 0}).encode())])
        with patch:
            pending = sync.get_pending()
        self.assertEqual(pending, [])
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(_ua(opener.requests[0]), cs.HTTP_USER_AGENT)

    def test_propose_banish_introduces_the_app(self):
        # THE GATE'S OWN LEG — the ask that never arrived now travels
        # under the app's name:
        sync = CloudflareSync(dict(_PAIRED))
        opener, patch = _capture([
            (200, json.dumps({'success': True, 'id': 'conf-1',
                              'message_id': 42}).encode())])
        with patch:
            conf_id, why = sync.propose_banish(
                1, [{'url': 'https://x.example/a', 'title': 'A'}])
        self.assertEqual(conf_id, 'conf-1')
        self.assertEqual(why, '')
        self.assertEqual(_ua(opener.requests[0]), cs.HTTP_USER_AGENT)
        # the ask's body still carries the round-trip payload:
        body = json.loads(opener.requests[0].data.decode('utf-8'))
        self.assertEqual(body['count'], 1)
        self.assertEqual(body['items'][0]['url'], 'https://x.example/a')

    def test_banish_status_and_result_wear_the_name(self):
        sync = CloudflareSync(dict(_PAIRED))
        opener, patch = _capture([
            (200, json.dumps({'success': True, 'status': 'confirmed',
                              'count': 1}).encode()),
            (200, json.dumps({'success': True, 'status': 'done'}).encode()),
        ])
        with patch:
            self.assertEqual(sync.banish_status('conf-1'), 'confirmed')
            self.assertTrue(sync.report_banish_result('conf-1', 'deleted', 1))
        for req in opener.requests:
            self.assertEqual(_ua(req), cs.HTTP_USER_AGENT)


class TestPairAndHealthWearTheName(unittest.TestCase):
    """The legs outside the HMAC family."""

    def test_pair_introduces_the_app(self):
        # the FIRST leg — pairing itself died under the banned
        # signature (the 403 the pair() error path used to blame on
        # redeploys and proxies):
        sync = CloudflareSync({
            'cloudflare_worker_url': _PAIRED['cloudflare_worker_url']})
        opener, patch = _capture([
            (200, json.dumps({'success': True, 'install_id': 'iid',
                              'shared_secret': 's' * 64}).encode())])
        with patch:
            ok, msg = sync.pair('ABC12345')
        self.assertTrue(ok, msg)
        self.assertEqual(_ua(opener.requests[0]), cs.HTTP_USER_AGENT)

    def test_health_check_introduces_the_app(self):
        sync = CloudflareSync(dict(_PAIRED))
        opener, patch = _capture([
            (200, json.dumps({'status': 'ok', 'version': '0.31.0'}).encode())])
        with patch:
            h = sync.health_check()
        self.assertEqual(h['version'], '0.31.0')
        self.assertEqual(_ua(opener.requests[0]), cs.HTTP_USER_AGENT)


class TestTheGateChannelWearsTheName(unittest.TestCase):
    """make_telegram_confirm — the channel the GUI worker injects; the
    whole round-trip (propose → poll → answer) rides the app's name."""

    def test_the_confirm_round_trip_introduces_the_app(self):
        from gitcurator.integrations import banish_confirm as bc
        cfg = dict(_PAIRED)
        cfg['banish_confirm_timeout_s'] = 30
        opener, patch = _capture([
            # propose -> success with an id
            (200, json.dumps({'success': True, 'id': 'conf-9',
                              'message_id': 7}).encode()),
            # first poll -> the owner pressed 🗑️ Delete
            (200, json.dumps({'success': True, 'status': 'confirmed',
                              'count': 2}).encode()),
            # the closing report
            (200, json.dumps({'success': True, 'status': 'done'}).encode()),
        ])
        with patch, mock.patch.object(bc, 'POLL_INTERVAL_S', 0):
            confirm = bc.make_telegram_confirm(cfg)
            self.assertIsNotNone(confirm)
            res = confirm(
                [{'url': 'https://x.example/a', 'title': 'A',
                  'marker': 'auto_delete', 'door': 'note tag'},
                 {'url': 'https://x.example/b', 'title': 'B',
                  'marker': 'delete', 'door': 'note tag'}])
            self.assertEqual(res['verdict'], 'confirmed')
            self.assertTrue(callable(res['report']))
            res['report'](2)
        self.assertEqual(len(opener.requests), 3)
        for req in opener.requests:
            self.assertEqual(_ua(req), cs.HTTP_USER_AGENT,
                             'every leg of the gate round-trip must '
                             'introduce the app')
        # the propose body carried BOTH items (the count the owner sees):
        body = json.loads(opener.requests[0].data.decode('utf-8'))
        self.assertEqual(body['count'], 2)
        self.assertEqual(len(body['items']), 2)


if __name__ == '__main__':
    unittest.main()

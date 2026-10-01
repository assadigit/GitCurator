"""Outbound TLS contexts behind one explicit config flag.

Historical context (v26 era): every outbound HTTPS path that is NOT the
websites fetcher (cloud LLM calls, banner downloads, the sources fetch)
disabled certificate verification inline — a silent hard-disable that
made sense on the owner's censored network (self-signed proxy roots,
self-hosted llama.cpp / LM Studio endpoints) but was invisible to the
user and untestable.

v0.26.0 — SWOT hardening: the behavior is unchanged by default but is
now EXPLICIT and switchable per the top-level ``verify_ssl`` config key
(default ``false`` — see config.example.json). Set it to ``true`` to
enforce certificate verification on these outbound calls. The websites
fetcher (core/web_fetch.py) has always verified certificates and is not
affected by this flag.

llm_client's cloud paths take a ``verify_tls`` keyword instead (they
are pure stdlib and parameter-driven); the desktop callers thread the
same config flag into them.
"""

import ssl
from typing import Any, Dict, Optional


def verify_ssl_enabled(config: Optional[Dict[str, Any]]) -> bool:
    """Read the explicit ``verify_ssl`` flag (default False = the
    historical censored-network behavior)."""
    return bool((config or {}).get('verify_ssl', False))


def outbound_ssl_context(config: Optional[Dict[str, Any]]) -> ssl.SSLContext:
    """A TLS context honoring the ``verify_ssl`` config flag.

    ``verify_ssl`` false/absent -> certificate verification disabled
    (the pre-v0.26.0 behavior, preserved by default). ``verify_ssl``
    true -> certificates are verified; a bad cert fails the call.
    """
    ctx = ssl.create_default_context()
    if not verify_ssl_enabled(config):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx

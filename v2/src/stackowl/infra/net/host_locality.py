"""Classify whether a configured base URL points at a self-hosted (local) backend.

Locality and routing *tier* are ORTHOGONAL: a self-hosted Ollama is ``tier: fast``
yet runs on the box. The authoritative, migration-free locality signal is the
provider's configured ``base_url`` host — a loopback / private / link-local target
(or the literal ``localhost``) means the backend is on-box and an image sent to it
never leaves the machine; anything else is treated as cloud (egress).

This is a pure, NON-blocking classifier: it inspects the host string only and does
NOT perform DNS resolution (resolution at config-build time would block and is
unnecessary — a hostname that resolves to a public IP is cloud by definition for
the purpose of egress disclosure). Pure stdlib (``ipaddress``); cross-platform.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from stackowl.infra.observability import log

__all__ = ["is_local_url"]

# Hostnames that always denote the local machine regardless of DNS.
_LOCAL_HOSTNAMES = frozenset({"localhost"})


def is_local_url(base_url: str | None) -> bool:
    """True iff ``base_url`` points at a self-hosted (on-box / private-network) target.

    A loopback / private / link-local IP literal, or the ``localhost`` hostname,
    classifies as LOCAL. A blank/unparseable URL, or any other (public) host, is
    NOT local. Never raises (B5) — an undecidable input fails safe to ``False``
    (treated as cloud, the more conservative egress disclosure).
    """
    if not base_url or not base_url.strip():
        return False
    try:
        host = (urlsplit(base_url).hostname or "").lower().rstrip(".")
    except Exception as exc:  # pragma: no cover — defensive; urlsplit is lenient.
        log.engine.debug(
            "[host_locality] unparseable base_url — treating as cloud",
            exc_info=exc,
        )
        return False
    if not host:
        return False
    if host in _LOCAL_HOSTNAMES:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # A non-literal hostname that is not a known local name → treat as cloud.
        return False
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local)

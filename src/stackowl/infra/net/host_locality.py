"""Classify whether a configured base URL points at a self-hosted (local) backend.

Locality and routing *tier* are ORTHOGONAL: a self-hosted Ollama is ``tier: fast``
yet runs on the box. The authoritative, migration-free locality signal is the
provider's configured ``base_url`` host — a loopback / private / link-local target
(or the literal ``localhost``) means the backend is self-hosted, and an image sent
to it never leaves the private network; anything else is treated as cloud (egress).

Two consumers, and they are why this must be right in BOTH directions:

* ``vision/selector.py`` — egress disclosure. Wrongly saying "local" would send an
  image off-network while telling the user it stayed.
* ``providers`` pricing (F128) — wrongly saying "cloud" invents money.

DNS RESOLUTION, added 2026-08-07. This module used to be purely syntactic, with
a stated reason: *"resolution at config-build time would block and is
unnecessary"*. The first half is a real constraint and is respected — nothing
here runs at config-build time. The second half was wrong, and it was measured:

    base_url  = http://llm-gateway.dev.nera.gov:4000/v1
    resolves  = 172.30.104.100          (RFC1918 — a private host)
    classified= CLOUD                    (hostname is not an IP literal)
    result    = every call billed at the unknown-cloud fallback rate
                ~$2,328 of imaginary spend in 10 days, 82,016 rows

Note what changes and what does not: an IP literal of ``172.30.104.100`` was
ALREADY classified local, so private-network targets have always counted. All
this does is make a hostname that resolves there classify the same as the
literal. It does not widen what "local" means.

Resolution is lazy (first use, never at import or config build), cached per host
for the process lifetime, and never raises — an unresolvable host fails safe to
``False`` (cloud), which is the conservative answer for the egress consumer.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from stackowl.infra.observability import log

__all__ = ["is_local_url"]

# Hostnames that always denote the local machine regardless of DNS.
_LOCAL_HOSTNAMES = frozenset({"localhost"})

#: host -> locality, so the resolver runs at most once per host per process.
#: Providers ask on every cost record; without this, pricing would issue a DNS
#: query per LLM call.
_RESOLVED: dict[str, bool] = {}


def _ip_is_local(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local)


def _resolve_is_local(host: str) -> bool:
    """Does ``host`` resolve to a private/loopback address? Cached; never raises.

    ALL resolved addresses must be local for the answer to be local. A host that
    round-robins between a private and a public address is reachable off-network,
    and for the egress consumer that means the image can leave — so the honest
    answer is cloud.
    """
    cached = _RESOLVED.get(host)
    if cached is not None:
        return cached
    try:
        infos = socket.getaddrinfo(host, None)
        addresses = {info[4][0] for info in infos}
        local = bool(addresses) and all(
            _ip_is_local(ipaddress.ip_address(addr)) for addr in addresses
        )
    except Exception as exc:  # noqa: BLE001 — B5: classification must never raise
        # Fail safe to CLOUD. A DNS failure is not evidence of locality, and for
        # the egress consumer guessing "local" is the harmful direction.
        log.engine.warning(
            "[host_locality] could not resolve host — treating as cloud",
            exc_info=exc,
            extra={"_fields": {"host": host}},
        )
        _RESOLVED[host] = False
        return False
    _RESOLVED[host] = local
    log.engine.info(
        "[host_locality] resolved host locality",
        extra={"_fields": {
            "host": host,
            "local": local,
            # Count only — the addresses themselves are infrastructure detail
            # and this line is written on every fresh process.
            "addresses": len(addresses),
        }},
    )
    return local


def is_local_url(base_url: str | None) -> bool:
    """True iff ``base_url`` points at a self-hosted (on-box / private-network) target.

    A loopback / private / link-local IP literal, the ``localhost`` hostname, or a
    hostname that RESOLVES entirely to such addresses, classifies as LOCAL. A
    blank/unparseable URL, or any host reachable at a public address, is NOT
    local. Never raises (B5) — an undecidable input fails safe to ``False``
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
        # A non-literal hostname: ask DNS. Cached, so this costs one lookup per
        # host per process rather than one per pricing decision.
        return _resolve_is_local(host)
    return _ip_is_local(ip)

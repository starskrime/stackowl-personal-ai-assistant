"""SSRF egress guard — validate an outbound URL before it is fetched.

Provenance: see ``_bmad-output/research/tool-port-analysis.md``. Adopts the
established *resolve-then-validate* pattern — resolve the host and reject if ANY
resolved IP is loopback / private / link-local / carrier-grade-NAT / multicast /
reserved / unspecified — plus an ``http(s)``-only scheme allow-list. This
defends against DNS-rebinding answers that point at internal infrastructure
(cloud metadata at ``169.254.169.254`` is link-local; the Alibaba metadata
literal ``100.100.100.200`` falls in the CGNAT range). Pure stdlib
(``ipaddress`` + ``socket``); cross-platform.

Known limitation (tracked): this validates at call time. A TTL-0 rebind between
this check and the socket connect, and per-redirect re-validation, need a pinned
resolver / proxy egress — a fast-follow for the fetch layer, not this guard.
"""

from __future__ import annotations

import contextlib
import ipaddress
import socket
from collections.abc import Callable
from typing import Any, NoReturn
from urllib.parse import urlsplit

from stackowl.exceptions import StackOwlError
from stackowl.infra.observability import log

__all__ = ["SsrfBlockedError", "SsrfGuard", "guard_playwright_navigation"]

_ALLOWED_SCHEMES = frozenset({"http", "https"})
# RFC 6598 carrier-grade NAT — NOT flagged by ipaddress.is_private on every
# Python; also covers the 100.100.100.200 cloud-metadata literal.
_CGNAT_V4 = ipaddress.ip_network("100.64.0.0/10")

_IpAddr = ipaddress.IPv4Address | ipaddress.IPv6Address


class SsrfBlockedError(StackOwlError):
    """Raised when a URL fails the SSRF egress policy."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"SSRF egress blocked: {reason}")
        self.url = url
        self.reason = reason


def _default_resolve(host: str) -> list[str]:
    """Resolve ``host`` to a list of IP strings (empty on failure)."""
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except OSError as exc:
        log.tool.debug(
            "[ssrf] resolve failed", exc_info=exc, extra={"_fields": {"host": host}},
        )
        return []
    return [str(info[4][0]) for info in infos]


class SsrfGuard:
    """Validates outbound URLs against an egress policy (fails closed on doubt).

    Shared infra: reused by web_fetch (E0), web_search (E6) and the media
    fetchers (E10). Inject ``resolve_fn`` to make DNS deterministic in tests.
    """

    def __init__(
        self,
        *,
        resolve_fn: Callable[[str], list[str]] | None = None,
        allowed_schemes: frozenset[str] = _ALLOWED_SCHEMES,
        allow_private: bool = False,
    ) -> None:
        self._resolve = resolve_fn or _default_resolve
        self._allowed_schemes = frozenset(s.lower() for s in allowed_schemes)
        self._allow_private = allow_private

    def is_allowed(self, url: str) -> tuple[bool, str | None]:
        """Non-raising variant: ``(True, None)`` or ``(False, reason)``."""
        try:
            self.validate(url)
        except SsrfBlockedError as exc:
            return False, exc.reason
        return True, None

    def validate(self, url: str) -> None:
        """Raise :class:`SsrfBlockedError` if ``url`` violates the egress policy."""
        # 1. ENTRY
        log.tool.debug("[ssrf] validate: entry", extra={"_fields": {"url_len": len(url)}})
        parts = urlsplit(url)
        scheme = (parts.scheme or "").lower()
        # 2. DECISION — scheme allow-list
        if scheme not in self._allowed_schemes:
            self._block(url, f"scheme '{scheme or '(none)'}' not allowed")
        host = parts.hostname
        if not host:
            self._block(url, "missing host")
        # Normalize: lowercase, drop a single FQDN-root trailing dot so the guard
        # classifies identically to the resolver the browser will use.
        host = host.lower().rstrip(".")
        if not host or any(c.isspace() for c in host):
            self._block(url, "invalid host")

        # 3. STEP — IP literal (incl. integer/hex/octal forms) needs no DNS;
        # otherwise resolve and check EVERY resolved IP (rebinding defense).
        literal = self._canonical_ip(host)
        if literal is not None:
            self._check_ip(url, literal)
        else:
            resolved = self._resolve(host)
            if not resolved:
                self._block(url, f"host '{host}' did not resolve")
            for ip_str in resolved:
                obj = self._as_ip(ip_str)
                if obj is None:
                    self._block(url, f"unparseable resolved address '{ip_str}'")
                self._check_ip(url, obj)
        # 4. EXIT
        log.tool.debug("[ssrf] validate: exit allowed", extra={"_fields": {"host": host}})

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _as_ip(self, value: str) -> _IpAddr | None:
        try:
            return ipaddress.ip_address(value)
        except ValueError:
            return None

    def _canonical_ip(self, host: str) -> _IpAddr | None:
        """Resolve IP-literal forms a browser accepts: dotted, decimal, hex, octal.

        ``http://2130706433/``, ``http://0x7f000001/`` and ``http://017700000001/``
        all denote 127.0.0.1 to a browser; canonicalize them so the guard cannot
        be bypassed with an alternate encoding (independent of the DNS resolver).
        """
        dotted = self._as_ip(host)
        if dotted is not None:
            return dotted
        value: int | None = None
        try:
            if host.startswith(("0x", "0X")):
                value = int(host, 16)
            elif host.startswith("0") and len(host) > 1 and all(c in "01234567" for c in host):
                value = int(host, 8)
            elif host.isdigit():
                value = int(host, 10)
        except ValueError:
            value = None
        if value is not None and 0 <= value <= 0xFFFFFFFF:
            return ipaddress.IPv4Address(value)
        return None

    def _check_ip(self, url: str, ip: _IpAddr) -> None:
        # Normalize IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1) to its IPv4 form.
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        reason = self._classify(ip)
        if reason is not None:
            self._block(url, f"resolved ip {ip} is {reason}")

    def _classify(self, ip: _IpAddr) -> str | None:
        if ip.is_loopback:
            return "loopback"
        if ip.is_link_local:  # 169.254.0.0/16 (incl. cloud metadata) + fe80::/10
            return "link-local"
        if ip.is_multicast:
            return "multicast"
        if ip.is_unspecified:
            return "unspecified"
        if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_V4:
            return "carrier-grade-nat"
        if ip.is_reserved:
            return "reserved"
        if not self._allow_private and ip.is_private:
            return "private"
        return None

    def _block(self, url: str, reason: str) -> NoReturn:
        log.tool.warning(
            "[ssrf] validate: BLOCKED", extra={"_fields": {"reason": reason}},
        )
        raise SsrfBlockedError(url, reason)


#: FX-05 — shared default so every caller re-validates against the same policy
#: unless it has a reason to inject its own (tests, a non-default allow_private).
_DEFAULT_GUARD = SsrfGuard()


async def guard_playwright_navigation(route: Any, *, guard: SsrfGuard | None = None) -> None:
    """Playwright route handler: re-validate every navigation/redirect hop.

    A pre-flight check on the initial URL is not enough — a public page can
    302 to ``http://169.254.169.254/`` (or DNS-rebind an allowed hostname to an
    internal IP) and the browser would follow it. Aborts any navigation
    (including redirect targets) whose URL fails the SSRF policy;
    non-navigation subresources pass through. Fails closed on error.

    Originally ``web_fetch``-only (E0-S2); shared here (FX-05) so the
    interactive ``browser_*`` session gets the same real IP-level defense
    instead of relying solely on its hostname allowlist (``is_domain_allowed``),
    which cannot see a redirect or DNS-rebind to an internal address.
    """
    g = guard or _DEFAULT_GUARD
    request = route.request
    try:
        if request.is_navigation_request():
            ok, reason = g.is_allowed(request.url)
            if not ok:
                log.tool.warning(
                    "[ssrf] guard_playwright_navigation: blocked navigation/redirect",
                    extra={"_fields": {"url": request.url, "reason": reason}},
                )
                await route.abort()
                return
    except Exception as exc:
        log.tool.warning(
            "[ssrf] guard_playwright_navigation: guard error — aborting (fail closed)",
            exc_info=exc,
        )
        with contextlib.suppress(Exception):
            await route.abort()
        return
    await route.continue_()

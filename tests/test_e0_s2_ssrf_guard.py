"""E0-S2 — shared SSRF egress guard.

Blocks outbound fetches to private/loopback/link-local/metadata ranges and
non-http(s) schemes, and defends against DNS-rebinding by resolving the host
and validating every resolved IP. See E0-S2-ssrf-egress-guard.md.
"""

from __future__ import annotations

import pytest

from stackowl.infra.net.ssrf_guard import SsrfBlockedError, SsrfGuard


def _guard(resolved: dict[str, list[str]] | None = None) -> SsrfGuard:
    """Guard with an injected resolver (no real DNS) mapping host -> IPs."""
    table = resolved or {}

    def _resolve(host: str) -> list[str]:
        return table.get(host, [])

    return SsrfGuard(resolve_fn=_resolve)


# --------------------------------------------------------------------------- #
# blocked IP ranges (host given as a literal IP — no DNS needed)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1:8080/admin",
        "http://10.0.0.5/",
        "http://10.255.255.255/",
        "http://172.16.0.1/",
        "http://172.31.255.255/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://169.254.1.1/",  # link-local
        "http://0.0.0.0/",  # unspecified
        "http://[::1]/",  # IPv6 loopback
        "http://[fc00::1]/",  # IPv6 ULA (private)
        "http://[fe80::1]/",  # IPv6 link-local
    ],
)
def test_blocks_private_and_special_ip_literals(url: str) -> None:
    with pytest.raises(SsrfBlockedError):
        _guard().validate(url)


# --------------------------------------------------------------------------- #
# blocked schemes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com/",
        "data:text/plain;base64,QQ==",
        "ssh://example.com/",
        "//example.com/",  # no scheme
    ],
)
def test_blocks_non_http_schemes(url: str) -> None:
    with pytest.raises(SsrfBlockedError):
        _guard({"example.com": ["93.184.216.34"]}).validate(url)


# --------------------------------------------------------------------------- #
# DNS rebinding — host resolves to a private IP
# --------------------------------------------------------------------------- #
def test_blocks_dns_rebinding_to_metadata() -> None:
    guard = _guard({"evil.example": ["169.254.169.254"]})
    with pytest.raises(SsrfBlockedError):
        guard.validate("https://evil.example/")


def test_blocks_when_any_resolved_ip_is_private() -> None:
    # public AND private in the answer set → must block (defense in depth)
    guard = _guard({"mixed.example": ["93.184.216.34", "10.0.0.1"]})
    with pytest.raises(SsrfBlockedError):
        guard.validate("https://mixed.example/")


def test_blocks_unresolvable_host() -> None:
    with pytest.raises(SsrfBlockedError):
        _guard({}).validate("https://nope.invalid/")


def test_blocks_missing_host() -> None:
    with pytest.raises(SsrfBlockedError):
        _guard().validate("http:///path-only")


# --------------------------------------------------------------------------- #
# allowed: public hosts / IPs
# --------------------------------------------------------------------------- #
def test_allows_public_ip_literal() -> None:
    _guard().validate("https://93.184.216.34/")  # no raise


def test_allows_public_host() -> None:
    guard = _guard({"example.com": ["93.184.216.34"]})
    guard.validate("https://example.com/path?q=1")  # no raise


def test_is_allowed_returns_reason_without_raising() -> None:
    ok, reason = _guard().is_allowed("http://127.0.0.1/")
    assert ok is False
    assert reason is not None and "loopback" in reason.lower() or "private" in (reason or "").lower()

    ok2, reason2 = _guard({"example.com": ["93.184.216.34"]}).is_allowed("https://example.com/")
    assert ok2 is True
    assert reason2 is None


def test_localhost_name_blocked_via_resolution() -> None:
    guard = _guard({"localhost": ["127.0.0.1"]})
    with pytest.raises(SsrfBlockedError):
        guard.validate("http://localhost:9000/")


# --------------------------------------------------------------------------- #
# bypass vectors (QA findings M2/M3 + classic tricks) — must NOT fail open
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "http://2130706433/",       # decimal int form of 127.0.0.1
        "http://0x7f000001/",       # hex form of 127.0.0.1
        "http://017700000001/",     # octal form of 127.0.0.1
        "http://0xA9FEA9FE/",       # hex form of 169.254.169.254 (metadata)
    ],
)
def test_blocks_integer_encoded_ip_literals(url: str) -> None:
    # Must block WITHOUT relying on the system resolver (injected empty resolver).
    with pytest.raises(SsrfBlockedError):
        _guard({}).validate(url)


def test_blocks_trailing_dot_host() -> None:
    # FQDN-root form must normalize identically and still block.
    with pytest.raises(SsrfBlockedError):
        _guard().validate("http://169.254.169.254./latest/meta-data/")


def test_blocks_userinfo_at_trick() -> None:
    # The real host is after the '@' — must be the one validated.
    with pytest.raises(SsrfBlockedError):
        _guard({"expected.com": ["93.184.216.34"]}).validate(
            "http://expected.com@169.254.169.254/"
        )


def test_blocks_cgnat_and_alibaba_metadata() -> None:
    with pytest.raises(SsrfBlockedError):
        _guard().validate("http://100.64.0.1/")
    with pytest.raises(SsrfBlockedError):
        _guard().validate("http://100.100.100.200/")  # Alibaba metadata (CGNAT range)


def test_blocks_ipv4_mapped_ipv6_loopback() -> None:
    with pytest.raises(SsrfBlockedError):
        _guard().validate("http://[::ffff:127.0.0.1]/")


def test_allows_public_host_with_trailing_dot() -> None:
    _guard({"example.com": ["93.184.216.34"]}).validate("https://example.com./")  # no raise


# --------------------------------------------------------------------------- #
# redirect re-validation (QA B1) — guard the navigation route handler in isolation
# --------------------------------------------------------------------------- #
async def test_navigation_guard_aborts_redirect_to_metadata() -> None:
    from stackowl.tools.io.web_fetch import _guard_navigation

    aborted: list[bool] = []
    continued: list[bool] = []

    class _Req:
        url = "http://169.254.169.254/latest/meta-data/"

        def is_navigation_request(self) -> bool:
            return True

    class _Route:
        request = _Req()

        async def abort(self, *a: object) -> None:
            aborted.append(True)

        async def continue_(self, *a: object) -> None:
            continued.append(True)

    await _guard_navigation(_Route())  # type: ignore[arg-type]
    assert aborted == [True]
    assert continued == []


async def test_navigation_guard_continues_public_navigation() -> None:
    from stackowl.tools.io.web_fetch import _guard_navigation

    continued: list[bool] = []

    class _Req:
        url = "https://93.184.216.34/page"

        def is_navigation_request(self) -> bool:
            return True

    class _Route:
        request = _Req()

        async def abort(self, *a: object) -> None:
            raise AssertionError("should not abort a public navigation")

        async def continue_(self, *a: object) -> None:
            continued.append(True)

    await _guard_navigation(_Route())  # type: ignore[arg-type]
    assert continued == [True]


# --------------------------------------------------------------------------- #
# integration: web_fetch tool rejects a metadata URL before touching the runtime
# --------------------------------------------------------------------------- #
async def test_web_fetch_rejects_metadata_url_before_navigation() -> None:
    from stackowl.tools.io.web_fetch import WebFetchTool

    tool = WebFetchTool()
    # No services / browser runtime wired — the SSRF guard must reject FIRST,
    # so we never reach the "runtime not initialized" path or any navigation.
    result = await tool.execute(url="http://169.254.169.254/latest/meta-data/")
    assert result.success is False
    assert "egress policy" in (result.error or "")


async def test_web_fetch_rejects_file_scheme() -> None:
    from stackowl.tools.io.web_fetch import WebFetchTool

    result = await WebFetchTool().execute(url="file:///etc/passwd")
    assert result.success is False
    assert "egress policy" in (result.error or "")

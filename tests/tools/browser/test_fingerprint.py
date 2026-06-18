"""Tests for browser/_fingerprint.py — captcha detection + domain allowlist."""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.tools.browser._fingerprint import detect_captcha, is_domain_allowed


class _FakeElement:
    def __init__(self, visible: bool = True) -> None:
        self._visible = visible

    async def is_visible(self) -> bool:
        return self._visible


class _FakePage:
    """Stub Playwright page that returns a fake element for a configured selector."""

    def __init__(self, match_selector: str | None, visible: bool = True) -> None:
        self._match = match_selector
        self._visible = visible

    async def query_selector(self, selector: str) -> Any:
        if self._match is None:
            return None
        if selector == self._match:
            return _FakeElement(visible=self._visible)
        return None


class TestDetectCaptcha:
    async def test_returns_none_when_no_captcha(self) -> None:
        page = _FakePage(match_selector=None)
        result = await detect_captcha(page)
        assert result is None

    async def test_detects_cloudflare_turnstile(self) -> None:
        page = _FakePage(match_selector='iframe[src*="challenges.cloudflare.com"]')
        result = await detect_captcha(page)
        assert result == "cloudflare_turnstile"

    async def test_detects_hcaptcha(self) -> None:
        page = _FakePage(match_selector='iframe[src*="hcaptcha.com"], div.h-captcha')
        result = await detect_captcha(page)
        assert result == "hcaptcha"

    async def test_ignores_hidden_widget(self) -> None:
        # A captcha widget that's present but hidden should still trigger —
        # the helper defaults to assuming visible on probe failure, but the
        # explicit False path must NOT return.
        page = _FakePage(
            match_selector='iframe[src*="challenges.cloudflare.com"]',
            visible=False,
        )
        result = await detect_captcha(page)
        assert result is None


class TestIsDomainAllowed:
    @pytest.mark.parametrize("url,allowed,expected", [
        ("https://example.com/x", ["example.com"], True),
        ("https://www.example.com/x", ["example.com"], True),
        ("https://api.example.com/v1", ["example.com"], True),
        ("https://evil.com/x", ["example.com"], False),
        ("https://example.com.evil.com/x", ["example.com"], False),
        ("https://example.com/x", [], True),       # empty allowlist == allow
        ("https://example.com/x", None, True),     # None allowlist == allow
        ("https://EXAMPLE.com/x", ["example.com"], True),
        ("https://example.com/x", [".example.com"], True),
    ])
    def test_match(self, url: str, allowed: list[str] | None, expected: bool) -> None:
        assert is_domain_allowed(url, allowed) is expected

    def test_url_without_host_denied_when_allowlist_set(self) -> None:
        assert is_domain_allowed("/relative/path", ["example.com"]) is False

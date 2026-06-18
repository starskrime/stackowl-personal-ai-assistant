"""OAUTH-1 (E-OAUTH) — headless device-code / OOB Google OAuth (no localhost hang).

The desktop OAuth flow opens a browser and BLOCKS on a localhost callback server —
on a headless host (no browser, unreachable loopback port) ``connect()`` hangs until
a 300s timeout. This adds a headless manual-copy (OOB-style) flow:

  * builds the consent URL with the OOB redirect (no localhost), prints it,
  * does NOT open a browser and does NOT bind any socket (so it can never hang on a
    callback), and
  * reads the user-pasted authorization code via an INJECTED ``code_reader`` and
    exchanges it for tokens.

If headless AND there is no way to obtain a code (no reader, no TTY), it raises an
honest error IMMEDIATELY rather than half-authenticating or hanging. The desktop
flow is unchanged. Tokens are stored via the existing OAuthManager under ~/.stackowl.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.integrations.google_oauth import GoogleOAuthFlow


class _FakeCreds:
    token = "access-tok"
    refresh_token = "refresh-tok"
    token_uri = "https://oauth2.googleapis.com/token"
    scopes = ["scope-a"]
    expiry = None


class _FakeFlow:
    """Stand-in for google_auth_oauthlib Flow — records calls, no network."""

    def __init__(self) -> None:
        self.redirect_uri: str | None = None
        self.fetched_code: str | None = None
        self.credentials = _FakeCreds()

    def authorization_url(self, **_kw: Any) -> tuple[str, str]:
        return ("https://accounts.google.com/o/oauth2/auth?x=1", "state")

    def fetch_token(self, *, code: str) -> None:
        self.fetched_code = code


def _flow(monkeypatch: pytest.MonkeyPatch, **kw: Any) -> tuple[GoogleOAuthFlow, _FakeFlow, list[str]]:
    fake = _FakeFlow()
    opened: list[str] = []
    f = GoogleOAuthFlow(
        client_id="cid",
        client_secret="csec",
        scopes=["scope-a"],
        flow_factory=lambda **_k: fake,
        browser_opener=lambda url: opened.append(url),
        **kw,
    )
    return f, fake, opened


def test_headless_flow_does_not_open_browser_or_bind_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []
    f, fake, opened = _flow(
        monkeypatch,
        headless=True,
        code_reader=lambda: "pasted-code-123",
        printer=prompts.append,
    )
    token = f.run()
    # No browser opened (headless), code exchanged via injected reader.
    assert opened == []
    assert fake.fetched_code == "pasted-code-123"
    # OOB redirect (no localhost) was used.
    assert "localhost" not in (fake.redirect_uri or "")
    # The consent URL was surfaced to the user for manual copy.
    assert any("accounts.google.com" in p for p in prompts)
    # Token bundle returned for persistence.
    assert token["token"] == "access-tok"
    assert token["refresh_token"] == "refresh-tok"


def test_headless_without_code_reader_or_tty_raises_not_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    f, _fake, _opened = _flow(monkeypatch, headless=True, code_reader=None, is_a_tty=False)
    # No way to get a code → honest immediate error, NEVER a hang / half-auth.
    with pytest.raises(RuntimeError) as ei:
        f.run()
    assert "code" in str(ei.value).lower()


def test_desktop_flow_uses_localhost_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    f, fake, opened = _flow(
        monkeypatch,
        headless=False,
        callback_waiter=lambda: "callback-code-456",
        printer=lambda _s: None,
    )
    token = f.run()
    # Desktop: browser opened, localhost redirect used, code from the callback.
    assert opened and opened[0].startswith("https://accounts.google.com")
    assert "localhost" in (fake.redirect_uri or "")
    assert fake.fetched_code == "callback-code-456"
    assert token["token"] == "access-tok"


def test_headless_detection_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # STACKOWL_HEADLESS_OAUTH=1 forces the headless path even on a desktop.
    monkeypatch.setenv("STACKOWL_HEADLESS_OAUTH", "1")
    assert GoogleOAuthFlow.detect_headless() is True
    monkeypatch.setenv("STACKOWL_HEADLESS_OAUTH", "0")
    # 0 = explicit desktop; detection falls through to DISPLAY/platform heuristics.
    monkeypatch.setenv("DISPLAY", ":0")
    assert GoogleOAuthFlow.detect_headless() is False

"""GoogleOAuthFlow — one OAuth flow for desktop AND headless hosts (OAUTH-1).

The original per-adapter ``connect()`` opened a browser and BLOCKED on a localhost
callback HTTP server. On a headless host (no browser, unreachable loopback port)
that hangs until a 300s timeout — and the Calendar adapter never even captured the
callback. This consolidates both adapters onto one injectable flow with two modes:

  * DESKTOP — open the system browser, bind a localhost callback, wait for the
    redirect (unchanged behaviour, now shared).
  * HEADLESS — manual-copy (OOB-style): build the consent URL with the OOB redirect
    (``urn:ietf:wg:oauth:2.0:oob``), PRINT it (no browser, NO socket bound → can
    never hang on a callback), and read the pasted code via an injected reader.

Fail-fast honesty: in headless mode with no code reader AND no TTY, it raises
IMMEDIATELY (never hangs, never half-authenticates). Every external dependency
(the google Flow, the browser opener, the callback waiter, the code reader, the
printer, the TTY probe) is injectable so the whole flow is unit-testable without a
network, a browser, or a real socket.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from collections.abc import Callable
from typing import Any

log = logging.getLogger("stackowl.integrations")

# The out-of-band redirect: Google returns the code on the consent page for the user
# to copy, instead of redirecting to a localhost callback. No socket is ever bound.
_OOB_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
_DEFAULT_CALLBACK_PORT = 8080
_LOCALHOST_REDIRECT = f"http://localhost:{_DEFAULT_CALLBACK_PORT}/oauth/callback"

# Injected dependency types.
FlowFactory = Callable[..., Any]
CallbackWaiter = Callable[[], str | None]
CodeReader = Callable[[], str]


class GoogleOAuthFlow:
    """Runs a Google OAuth consent flow, desktop or headless, returning a token dict."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        scopes: list[str],
        headless: bool | None = None,
        flow_factory: FlowFactory | None = None,
        browser_opener: Callable[[str], None] | None = None,
        callback_waiter: CallbackWaiter | None = None,
        code_reader: CodeReader | None = None,
        printer: Callable[[str], None] | None = None,
        is_a_tty: bool | None = None,
        callback_port: int = _DEFAULT_CALLBACK_PORT,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = scopes
        self._headless = self.detect_headless() if headless is None else headless
        self._flow_factory = flow_factory
        self._browser_opener = browser_opener
        self._callback_waiter = callback_waiter
        self._code_reader = code_reader
        self._printer = printer or (lambda s: print(s))  # noqa: T201 — user-facing
        self._is_a_tty = sys.stdin.isatty() if is_a_tty is None else is_a_tty
        self._callback_port = callback_port

    # ------------------------------------------------------------------
    @staticmethod
    def detect_headless() -> bool:
        """True when this host cannot run an interactive browser-callback flow.

        Honors an explicit ``STACKOWL_HEADLESS_OAUTH`` override (``1``/``0``), else
        infers from the platform: a POSIX host with no ``DISPLAY``/``WAYLAND_DISPLAY``
        is headless; macOS/Windows are assumed to have a usable browser.
        """
        override = os.environ.get("STACKOWL_HEADLESS_OAUTH")
        if override is not None:
            return override.strip() == "1"
        if sys.platform in ("darwin", "win32"):
            return False
        # POSIX: a graphical session sets DISPLAY (X11) or WAYLAND_DISPLAY.
        return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    # ------------------------------------------------------------------
    def _build_flow(self, redirect_uri: str) -> Any:
        if self._flow_factory is not None:
            flow = self._flow_factory(
                client_id=self._client_id,
                client_secret=self._client_secret,
                scopes=self._scopes,
                redirect_uri=redirect_uri,
            )
            # Make the redirect observable for callers/tests that build the flow
            # themselves but still want to record which redirect was chosen.
            with contextlib.suppress(Exception):
                flow.redirect_uri = redirect_uri
            return flow
        # Production path — real google_auth_oauthlib Flow.
        try:
            from google_auth_oauthlib.flow import Flow
        except ImportError as exc:
            log.error("integrations.google_oauth: google_auth_oauthlib missing", exc_info=exc)
            raise RuntimeError(
                "google-auth-oauthlib is required for Google OAuth"
            ) from exc
        config = {
            "installed": {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uris": [redirect_uri],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        return Flow.from_client_config(config, scopes=self._scopes, redirect_uri=redirect_uri)

    def run(self) -> dict[str, Any]:
        """Execute the flow and return a token dict ready for OAuthManager.save."""
        log.debug(
            "integrations.google_oauth.run: entry",
            extra={"_fields": {"headless": self._headless}},
        )
        code = self._run_headless() if self._headless else self._run_desktop()
        return self._exchange(code)

    # ------------------------------------------------------------------
    def _run_headless(self) -> str:
        """Manual-copy flow — print the URL, read the pasted code. No socket bound."""
        flow = self._build_flow(_OOB_REDIRECT_URI)
        auth_url, _state = flow.authorization_url(prompt="consent")
        self._flow = flow  # exchange() reuses the same flow object
        self._printer(
            "This host is headless. Open this URL on any device, approve access, "
            "then paste the authorization code back here:\n" + auth_url
        )
        reader = self._code_reader
        if reader is None:
            # No injected reader: fall back to stdin ONLY if interactive — otherwise
            # raise immediately (NEVER hang, NEVER half-authenticate).
            if not self._is_a_tty:
                log.warning("integrations.google_oauth: headless, no code source — refusing to hang")
                raise RuntimeError(
                    "Headless OAuth needs the authorization code, but there is no "
                    "interactive terminal to paste it into. Re-run /connect from an "
                    "interactive session, or provide the code programmatically."
                )
            reader = lambda: input("Authorization code: ").strip()  # noqa: E731
        code = reader()
        if not code:
            raise RuntimeError("No authorization code was provided — aborting OAuth.")
        log.debug("integrations.google_oauth._run_headless: code received")
        return code

    def _run_desktop(self) -> str:
        """Browser + localhost callback flow (the original desktop behaviour)."""
        flow = self._build_flow(_LOCALHOST_REDIRECT)
        auth_url, _state = flow.authorization_url(prompt="consent")
        self._flow = flow
        opener = self._browser_opener
        if opener is None:
            import webbrowser

            def opener(url: str) -> None:
                webbrowser.open(url)

        opener(auth_url)
        waiter = self._callback_waiter
        if waiter is None:
            waiter = self._default_callback_waiter
        code = waiter()
        if not code:
            raise RuntimeError("OAuth flow timed out or was cancelled.")
        log.debug("integrations.google_oauth._run_desktop: code received from callback")
        return code

    def _default_callback_waiter(self) -> str | None:
        """Bind a localhost HTTP server and wait for the OAuth redirect (desktop)."""
        import http.server
        import time
        import urllib.parse

        code_holder: list[str | None] = [None]

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if "code" in params:
                    code_holder[0] = params["code"][0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<html><body>Auth complete. Return to StackOwl.</body></html>")

            def log_message(self, *_a: Any) -> None:
                pass

        server = http.server.HTTPServer(("localhost", self._callback_port), _Handler)
        server.timeout = 1.0
        deadline = time.monotonic() + 300
        try:
            while time.monotonic() < deadline:
                server.handle_request()
                if code_holder[0] is not None:
                    break
        finally:
            server.server_close()
        return code_holder[0]

    def _exchange(self, code: str) -> dict[str, Any]:
        """Exchange the auth code for tokens and return a serializable bundle."""
        flow = self._flow
        flow.fetch_token(code=code)
        creds = flow.credentials
        token: dict[str, Any] = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "scopes": list(creds.scopes) if creds.scopes else [],
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
        }
        log.info("integrations.google_oauth._exchange: tokens obtained")
        return token

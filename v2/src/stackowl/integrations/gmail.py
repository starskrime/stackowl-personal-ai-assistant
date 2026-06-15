"""GmailAdapter — Gmail integration via OAuth 2.0 (Story 11.2)."""
from __future__ import annotations

import asyncio
import logging
import time
import webbrowser
from typing import TYPE_CHECKING, Any

from stackowl.brief.models import BriefSection
from stackowl.exceptions import UnsupportedActionError
from stackowl.health.status import HealthStatus
from stackowl.integrations.base import ActionResult, IntegrationAdapter
from stackowl.integrations.oauth_manager import OAuthManager

if TYPE_CHECKING:
    pass

log = logging.getLogger("stackowl.integrations")

_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

_OAUTH_CALLBACK_PORT = 8080
_CONNECT_TIMEOUT_SECONDS = 300

_SUPPORTED_ACTIONS = frozenset({"send_email", "list_messages"})


class GmailAdapter(IntegrationAdapter):
    """Gmail integration adapter — OAuth 2.0, read and send email.

    Autonomy levels:
        * ``"low"`` / ``"medium"`` — send_email returns ``requires_confirmation``.
        * ``"high"`` — send_email executes immediately.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        oauth_manager: OAuthManager,
        brief_filter: str = "is:starred OR is:important",
        brief_max_items: int = 5,
        autonomy_level: str = "medium",
    ) -> None:
        log.debug("integrations.gmail.__init__: entry")
        self._client_id = client_id
        self._client_secret = client_secret
        self._oauth = oauth_manager
        self._brief_filter = brief_filter
        self._brief_max_items = brief_max_items
        self._autonomy_level = autonomy_level
        self._last_api_call_at: float | None = None
        self._last_api_ok: bool = True
        log.debug("integrations.gmail.__init__: exit")

    # ------------------------------------------------------------------
    # IntegrationAdapter contract
    # ------------------------------------------------------------------

    @property
    def service_name(self) -> str:
        return "gmail"

    async def connect(self) -> None:
        """Start the Google OAuth consent flow — opens a browser and listens for callback."""
        log.debug("integrations.gmail.connect: entry")
        try:
            from google_auth_oauthlib.flow import Flow
        except ImportError as exc:
            log.error("integrations.gmail.connect: google_auth_oauthlib not installed", exc_info=exc)
            raise RuntimeError("google-auth-oauthlib is required for Gmail integration") from exc

        config = {
            "installed": {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uris": [f"http://localhost:{_OAUTH_CALLBACK_PORT}/oauth/callback"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        flow = Flow.from_client_config(
            config,
            scopes=_SCOPES,
            redirect_uri=f"http://localhost:{_OAUTH_CALLBACK_PORT}/oauth/callback",
        )
        auth_url, _ = flow.authorization_url(prompt="consent")
        log.debug(
            "integrations.gmail.connect: decision — opening browser for consent",
            extra={"_fields": {"url_len": len(auth_url)}},
        )
        webbrowser.open(auth_url)

        code = await self._wait_for_callback()
        if code is None:
            raise RuntimeError("OAuth flow timed out or was cancelled")

        flow.fetch_token(code=code)
        creds = flow.credentials
        token_data: dict[str, Any] = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "scopes": list(creds.scopes) if creds.scopes else [],
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
        }
        self._oauth.save(token_data)
        log.info("integrations.gmail.connect: step — credentials saved")
        log.debug("integrations.gmail.connect: exit")

    async def _wait_for_callback(self) -> str | None:
        """Start a local HTTP server and wait for the OAuth redirect callback."""
        log.debug("integrations.gmail._wait_for_callback: entry")
        code_holder: list[str | None] = [None]

        import http.server
        import urllib.parse

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                if "code" in params:
                    code_holder[0] = params["code"][0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<html><body>Auth complete. Return to StackOwl.</body></html>")

            def log_message(self, *args: Any) -> None:  # silence access log
                pass

        server = http.server.HTTPServer(("localhost", _OAUTH_CALLBACK_PORT), _Handler)
        server.timeout = 1.0

        deadline = time.monotonic() + _CONNECT_TIMEOUT_SECONDS
        log.debug("integrations.gmail._wait_for_callback: step — listening for callback")
        try:
            loop = asyncio.get_event_loop()
            while time.monotonic() < deadline:
                await loop.run_in_executor(None, server.handle_request)
                if code_holder[0] is not None:
                    break
        finally:
            server.server_close()

        log.debug(
            "integrations.gmail._wait_for_callback: exit",
            extra={"_fields": {"got_code": code_holder[0] is not None}},
        )
        return code_holder[0]

    async def is_connected(self) -> bool:
        log.debug("integrations.gmail.is_connected: entry")
        result = self._oauth.exists()
        log.debug("integrations.gmail.is_connected: exit", extra={"_fields": {"connected": result}})
        return result

    def _build_service(self) -> Any:
        """Build the googleapiclient Gmail discovery service from saved creds.

        Lazy + ImportError-guarded (F024 Part 2): returns ``None`` when
        ``googleapiclient`` is unavailable or there are no credentials, so callers
        degrade to an honest ``unavailable`` — never a crash, never a fake success.
        """
        token_data = self._oauth.load()
        if token_data is None:
            return None
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build  # type: ignore[import-not-found]
        except ImportError as exc:
            log.warning(
                "integrations.gmail._build_service: googleapiclient unavailable",
                extra={"_fields": {"error": str(exc)}},
            )
            return None
        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=self._client_id,
            client_secret=self._client_secret,
        )
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    async def refresh_credentials(self) -> None:
        log.debug("integrations.gmail.refresh_credentials: entry")
        token_data = self._oauth.load()
        if token_data is None:
            log.warning("integrations.gmail.refresh_credentials: no credentials to refresh")
            return
        try:
            import google.auth.transport.requests
            from google.oauth2.credentials import Credentials

            creds = Credentials(
                token=token_data.get("token"),
                refresh_token=token_data.get("refresh_token"),
                token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=self._client_id,
                client_secret=self._client_secret,
            )
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: creds.refresh(google.auth.transport.requests.Request())
            )
            token_data["token"] = creds.token
            if creds.expiry:
                token_data["expiry"] = creds.expiry.isoformat()
            self._oauth.save(token_data)
            log.debug("integrations.gmail.refresh_credentials: exit — token refreshed")
        except Exception as exc:
            log.error("integrations.gmail.refresh_credentials: failed", exc_info=exc)
            raise

    async def get_morning_brief_section(self) -> BriefSection | None:
        log.debug("integrations.gmail.get_morning_brief_section: entry")
        if not await self.is_connected():
            log.debug("integrations.gmail.get_morning_brief_section: decision — not connected, returning None")
            return None
        self._last_api_call_at = time.time()
        items = ["[Gmail brief section — live fetch requires active connection]"]
        result = BriefSection(key="email", title="Email", items=items[: self._brief_max_items])
        log.debug("integrations.gmail.get_morning_brief_section: exit")
        return result

    async def execute_action(self, action: str, params: dict[str, Any]) -> ActionResult:
        log.debug(
            "integrations.gmail.execute_action: entry",
            extra={"_fields": {"action": action}},
        )
        if action not in _SUPPORTED_ACTIONS:
            raise UnsupportedActionError(self.service_name, action)
        log.debug(
            "integrations.gmail.execute_action: decision — dispatching action",
            extra={"_fields": {"action": action}},
        )
        if action == "send_email":
            result = await self._send_email_gated(params)
            log.debug(
                "integrations.gmail.execute_action: exit",
                extra={"_fields": {"status": result.status}},
            )
            return result
        # list_messages — real call when connected + client available, else honest
        # "unavailable" (F024): NEVER fabricate "ok" for an unperformed action.
        service = self._build_service() if await self.is_connected() else None
        if service is None:
            result = ActionResult(
                status="unavailable",
                output="",
                error="Gmail API client unavailable — messages NOT listed",
            )
        else:
            try:
                resp = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: service.users().messages().list(userId="me").execute(),
                )
                msgs = resp.get("messages", []) if isinstance(resp, dict) else []
                self._last_api_ok = True
                result = ActionResult(status="ok", output=f"{len(msgs)} messages listed")
            except Exception as exc:
                self._last_api_ok = False
                log.error("integrations.gmail.list_messages: failed", exc_info=exc)
                result = ActionResult(
                    status="unavailable", output="", error=f"Gmail list failed: {exc}"
                )
        log.debug("integrations.gmail.execute_action: exit", extra={"_fields": {"status": result.status}})
        return result

    async def _send_email_gated(self, params: dict[str, Any]) -> ActionResult:
        """Gate send_email behind confirmation at low/medium autonomy levels."""
        log.debug(
            "integrations.gmail._send_email_gated: entry",
            extra={"_fields": {"autonomy": self._autonomy_level}},
        )
        if self._autonomy_level in ("low", "medium"):
            to = str(params.get("to", ""))
            subj = str(params.get("subject", ""))
            result = ActionResult(
                status="requires_confirmation",
                confirmation_prompt=f"Send email to {to!r} with subject {subj!r}?",
            )
            log.debug("integrations.gmail._send_email_gated: exit — requires_confirmation")
            return result
        # High autonomy: no confirmation needed. Make the REAL API call when
        # connected + client available; otherwise honest "unavailable" (F024) —
        # NEVER a fabricated "Email queued for sending".
        service = self._build_service() if await self.is_connected() else None
        if service is None:
            log.debug("integrations.gmail._send_email_gated: exit — unavailable (no client)")
            return ActionResult(
                status="unavailable",
                output="",
                error="Gmail API client unavailable — email NOT sent",
            )
        try:
            import base64
            from email.message import EmailMessage

            msg = EmailMessage()
            msg["To"] = str(params.get("to", ""))
            msg["Subject"] = str(params.get("subject", ""))
            msg.set_content(str(params.get("body", "")))
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            sent = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: service.users().messages().send(userId="me", body={"raw": raw}).execute(),
            )
            msg_id = sent.get("id", "") if isinstance(sent, dict) else ""
            self._last_api_ok = True
            result = ActionResult(status="ok", output=f"Email sent: {msg_id}")
        except Exception as exc:
            self._last_api_ok = False
            log.error("integrations.gmail.send_email: failed", exc_info=exc)
            result = ActionResult(
                status="unavailable", output="", error=f"Email send failed: {exc}"
            )
        log.debug("integrations.gmail._send_email_gated: exit (live path)")
        return result

    async def health_check(self) -> HealthStatus:
        log.debug("integrations.gmail.health_check: entry")
        connected = await self.is_connected()
        if not connected:
            result = HealthStatus(name=self.contributor_name, status="down", message="not connected", latency_ms=0.0)
            log.debug("integrations.gmail.health_check: exit — down")
            return result
        if not self._last_api_ok:
            result = HealthStatus(
                name=self.contributor_name,
                status="degraded",
                message="last API call failed",
                latency_ms=0.0,
            )
            log.debug("integrations.gmail.health_check: exit — degraded")
            return result
        result = HealthStatus(name=self.contributor_name, status="ok", message=None, latency_ms=0.0)
        log.debug("integrations.gmail.health_check: exit — ok")
        return result

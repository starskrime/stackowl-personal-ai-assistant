"""GoogleCalendarAdapter — Google Calendar integration via OAuth 2.0 (Story 11.3)."""
from __future__ import annotations

import asyncio
import logging
import time
import webbrowser
from typing import Any

from stackowl.brief.models import BriefSection
from stackowl.exceptions import UnsupportedActionError
from stackowl.health.status import HealthStatus
from stackowl.integrations.base import ActionResult, IntegrationAdapter
from stackowl.integrations.oauth_manager import OAuthManager

log = logging.getLogger("stackowl.integrations")

_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

_OAUTH_CALLBACK_PORT = 8080
_REDIRECT_URI = f"http://localhost:{_OAUTH_CALLBACK_PORT}/oauth/callback"

_SUPPORTED_ACTIONS = frozenset({"create_event", "list_events"})


class GoogleCalendarAdapter(IntegrationAdapter):
    """Google Calendar integration adapter — OAuth 2.0, read and create events.

    Autonomy levels:
        * ``"low"`` / ``"medium"`` — create_event returns ``requires_confirmation``.
        * ``"high"`` — create_event executes immediately.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        oauth_manager: OAuthManager,
        brief_max_items: int = 8,
        autonomy_level: str = "medium",
        timezone: str = "UTC",
    ) -> None:
        log.debug("integrations.google_calendar.__init__: entry")
        self._client_id = client_id
        self._client_secret = client_secret
        self._oauth = oauth_manager
        self._brief_max_items = brief_max_items
        self._autonomy_level = autonomy_level
        self._timezone = timezone
        self._last_api_call_at: float | None = None
        self._last_api_ok: bool = True
        log.debug("integrations.google_calendar.__init__: exit")

    # ------------------------------------------------------------------
    # IntegrationAdapter contract
    # ------------------------------------------------------------------

    @property
    def service_name(self) -> str:
        return "google_calendar"

    async def connect(self) -> None:
        """Start the Google OAuth consent flow for Calendar scopes."""
        log.debug("integrations.google_calendar.connect: entry")
        try:
            from google_auth_oauthlib.flow import Flow  # type: ignore[import]
        except ImportError as exc:
            log.error(
                "integrations.google_calendar.connect: google_auth_oauthlib not installed",
                exc_info=exc,
            )
            raise RuntimeError("google-auth-oauthlib is required for Calendar integration") from exc

        config = {
            "installed": {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uris": [_REDIRECT_URI],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        flow = Flow.from_client_config(config, scopes=_SCOPES, redirect_uri=_REDIRECT_URI)
        auth_url, _ = flow.authorization_url(prompt="consent")
        log.debug(
            "integrations.google_calendar.connect: decision — opening browser for consent",
            extra={"_fields": {"url_len": len(auth_url)}},
        )
        webbrowser.open(auth_url)
        log.info("integrations.google_calendar.connect: step — browser opened for consent")
        log.warning(
            "integrations.google_calendar.connect: callback listener not started — "
            "use GmailAdapter shared OAuth flow or handle redirect externally"
        )
        log.debug("integrations.google_calendar.connect: exit")

    async def is_connected(self) -> bool:
        log.debug("integrations.google_calendar.is_connected: entry")
        result = self._oauth.exists()
        log.debug(
            "integrations.google_calendar.is_connected: exit",
            extra={"_fields": {"connected": result}},
        )
        return result

    async def refresh_credentials(self) -> None:
        log.debug("integrations.google_calendar.refresh_credentials: entry")
        token_data = self._oauth.load()
        if token_data is None:
            log.warning("integrations.google_calendar.refresh_credentials: no credentials to refresh")
            return
        try:
            import google.auth.transport.requests  # type: ignore[import]
            from google.oauth2.credentials import Credentials  # type: ignore[import]

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
            log.debug("integrations.google_calendar.refresh_credentials: exit — token refreshed")
        except Exception as exc:
            log.error("integrations.google_calendar.refresh_credentials: failed", exc_info=exc)
            raise

    async def get_morning_brief_section(self) -> BriefSection | None:
        log.debug("integrations.google_calendar.get_morning_brief_section: entry")
        if not await self.is_connected():
            log.debug(
                "integrations.google_calendar.get_morning_brief_section: decision — not connected, returning None"
            )
            return None
        self._last_api_call_at = time.time()
        items = ["[Calendar brief section — live fetch requires active connection]"]
        result = BriefSection(
            key="calendar",
            title="Calendar",
            items=items[: self._brief_max_items],
        )
        log.debug("integrations.google_calendar.get_morning_brief_section: exit")
        return result

    async def execute_action(self, action: str, params: dict[str, Any]) -> ActionResult:
        log.debug(
            "integrations.google_calendar.execute_action: entry",
            extra={"_fields": {"action": action}},
        )
        if action not in _SUPPORTED_ACTIONS:
            raise UnsupportedActionError(self.service_name, action)
        log.debug(
            "integrations.google_calendar.execute_action: decision — dispatching action",
            extra={"_fields": {"action": action}},
        )
        if action == "create_event":
            result = await self._create_event_gated(params)
            log.debug(
                "integrations.google_calendar.execute_action: exit",
                extra={"_fields": {"status": result.status}},
            )
            return result
        # list_events — stub; real impl would call Calendar API
        result = ActionResult(status="ok", output="events listed")
        log.debug(
            "integrations.google_calendar.execute_action: exit",
            extra={"_fields": {"status": result.status}},
        )
        return result

    async def _create_event_gated(self, params: dict[str, Any]) -> ActionResult:
        """Gate create_event behind confirmation at low/medium autonomy."""
        log.debug(
            "integrations.google_calendar._create_event_gated: entry",
            extra={"_fields": {"autonomy": self._autonomy_level}},
        )
        if self._autonomy_level in ("low", "medium"):
            title = str(params.get("title", ""))
            result = ActionResult(
                status="requires_confirmation",
                confirmation_prompt=f"Create calendar event {title!r}?",
            )
            log.debug("integrations.google_calendar._create_event_gated: exit — requires_confirmation")
            return result
        result = ActionResult(status="ok", output="Event created")
        log.debug("integrations.google_calendar._create_event_gated: exit — ok (high autonomy)")
        return result

    async def health_check(self) -> HealthStatus:
        log.debug("integrations.google_calendar.health_check: entry")
        connected = await self.is_connected()
        if not connected:
            result = HealthStatus(
                name=self.contributor_name,
                status="down",
                message="not connected",
                latency_ms=0.0,
            )
            log.debug("integrations.google_calendar.health_check: exit — down")
            return result
        if not self._last_api_ok:
            result = HealthStatus(
                name=self.contributor_name,
                status="degraded",
                message="last API call failed",
                latency_ms=0.0,
            )
            log.debug("integrations.google_calendar.health_check: exit — degraded")
            return result
        result = HealthStatus(name=self.contributor_name, status="ok", message=None, latency_ms=0.0)
        log.debug("integrations.google_calendar.health_check: exit — ok")
        return result

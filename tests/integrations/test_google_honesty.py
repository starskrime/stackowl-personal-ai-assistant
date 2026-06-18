"""F024 Part 1 — Google Calendar/Gmail must NOT fabricate success.

Both adapters returned ``ActionResult(status="ok", output="Event created" / ...)``
for actions they never performed (no API call). A high-autonomy ``create_event`` /
``send_email`` LIED, and — critically — that fake ``ok`` laundered a failed
consequential action past the consequential ledger, so the C8 honest give-up floor
never fired. Part 1 makes every unperformed action honestly ``unavailable``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.integrations.gmail import GmailAdapter
from stackowl.integrations.google_calendar import GoogleCalendarAdapter
from stackowl.integrations.oauth_manager import OAuthManager


def _oauth(tmp_path: Path, service: str) -> OAuthManager:
    return OAuthManager(service_name=service, credentials_dir=tmp_path, master_key="k" * 32)


def _calendar(tmp_path: Path) -> GoogleCalendarAdapter:
    return GoogleCalendarAdapter(
        client_id="cid",
        client_secret="sec",
        oauth_manager=_oauth(tmp_path, "google_calendar"),
        autonomy_level="high",
    )


def _gmail(tmp_path: Path) -> GmailAdapter:
    return GmailAdapter(
        client_id="cid",
        client_secret="sec",
        oauth_manager=_oauth(tmp_path, "gmail"),
        autonomy_level="high",
    )


# =========================================================================== #
# Calendar — no fabricated "ok"/"Event created"
# =========================================================================== #


@pytest.mark.asyncio
async def test_calendar_create_event_high_autonomy_is_unavailable_not_ok(tmp_path: Path) -> None:
    cal = _calendar(tmp_path)
    result = await cal.execute_action("create_event", {"title": "Standup"})
    assert result.status != "ok", "must NOT fabricate success for an unperformed create"
    assert result.status == "unavailable"
    assert "Event created" not in (result.output or "")
    assert result.error  # an honest reason is present


@pytest.mark.asyncio
async def test_calendar_list_events_is_unavailable_not_ok(tmp_path: Path) -> None:
    cal = _calendar(tmp_path)
    result = await cal.execute_action("list_events", {})
    assert result.status == "unavailable"
    assert "events listed" not in (result.output or "")


@pytest.mark.asyncio
async def test_calendar_morning_brief_is_none_when_not_connected(tmp_path: Path) -> None:
    cal = _calendar(tmp_path)
    section = await cal.get_morning_brief_section()
    assert section is None  # honest not-connected contract, no placeholder text


# =========================================================================== #
# Gmail — no fabricated "ok"/"Email queued"
# =========================================================================== #


@pytest.mark.asyncio
async def test_gmail_send_email_high_autonomy_is_unavailable_not_ok(tmp_path: Path) -> None:
    gm = _gmail(tmp_path)
    result = await gm.execute_action("send_email", {"to": "a@b.c", "subject": "hi"})
    assert result.status != "ok"
    assert result.status == "unavailable"
    assert "queued" not in (result.output or "").casefold()
    assert result.error


@pytest.mark.asyncio
async def test_gmail_list_messages_is_unavailable_not_ok(tmp_path: Path) -> None:
    gm = _gmail(tmp_path)
    result = await gm.execute_action("list_messages", {})
    assert result.status == "unavailable"
    assert "messages listed" not in (result.output or "")


@pytest.mark.asyncio
async def test_gmail_morning_brief_is_none_when_not_connected(tmp_path: Path) -> None:
    gm = _gmail(tmp_path)
    section = await gm.get_morning_brief_section()
    assert section is None


# =========================================================================== #
# F024 Part 2 — with a live (mocked) discovery service, real ids come back
# =========================================================================== #


class _FakeCalendarService:
    """Minimal googleapiclient.discovery-shaped fake for events()."""

    def events(self):  # noqa: ANN201
        class _Events:
            def insert(self_inner, calendarId, body):  # noqa: ANN001, ANN202, N803
                class _Req:
                    def execute(self_req):  # noqa: ANN202
                        return {"id": "evt-123", "htmlLink": "http://x"}
                return _Req()

            def list(self_inner, **kwargs):  # noqa: ANN001, ANN202
                class _Req:
                    def execute(self_req):  # noqa: ANN202
                        return {"items": [{"id": "e1", "summary": "Standup"}]}
                return _Req()
        return _Events()


class _FakeGmailService:
    def users(self):  # noqa: ANN201
        class _Users:
            def messages(self_inner):  # noqa: ANN202
                class _Messages:
                    def send(self_m, userId, body):  # noqa: ANN001, ANN202, N803
                        class _Req:
                            def execute(self_req):  # noqa: ANN202
                                return {"id": "msg-456"}
                        return _Req()

                    def list(self_m, **kwargs):  # noqa: ANN202
                        class _Req:
                            def execute(self_req):  # noqa: ANN202
                                return {"messages": [{"id": "m1"}]}
                        return _Req()
                return _Messages()
        return _Users()


@pytest.mark.asyncio
async def test_calendar_create_event_with_live_service_returns_event_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cal = _calendar(tmp_path)
    monkeypatch.setattr(cal, "is_connected", lambda: _async_true())
    monkeypatch.setattr(cal, "_build_service", lambda: _FakeCalendarService())
    result = await cal.execute_action("create_event", {"title": "Standup"})
    assert result.status == "ok"
    assert "evt-123" in (result.output or "")


@pytest.mark.asyncio
async def test_gmail_send_email_with_live_service_returns_message_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gm = _gmail(tmp_path)
    monkeypatch.setattr(gm, "is_connected", lambda: _async_true())
    monkeypatch.setattr(gm, "_build_service", lambda: _FakeGmailService())
    result = await gm.execute_action(
        "send_email", {"to": "a@b.c", "subject": "hi", "body": "yo"}
    )
    assert result.status == "ok"
    assert "msg-456" in (result.output or "")


async def _async_true() -> bool:
    return True

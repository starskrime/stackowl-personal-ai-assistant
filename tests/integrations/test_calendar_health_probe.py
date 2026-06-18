"""OPS-7 (F023) — Calendar health_check runs a real probe; no false-green.

health_check returned "ok" whenever a token merely existed (``_last_api_ok``
was True at construction and never flipped on a real failure), so a Calendar
adapter with a broken API still presented as healthy. health_check now performs
a lightweight ``calendarList().list(maxResults=1)`` probe and reports from its
real result — never "ok" until an authenticated call has actually succeeded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.integrations.google_calendar import GoogleCalendarAdapter
from stackowl.integrations.oauth_manager import OAuthManager


def _oauth(tmp_path: Path) -> OAuthManager:
    return OAuthManager(
        service_name="google_calendar", credentials_dir=tmp_path, master_key="k" * 32
    )


def _calendar(tmp_path: Path) -> GoogleCalendarAdapter:
    return GoogleCalendarAdapter(
        client_id="cid",
        client_secret="sec",
        oauth_manager=_oauth(tmp_path),
        autonomy_level="high",
    )


async def _async_true() -> bool:
    return True


class _OkProbeService:
    """calendarList().list() succeeds — a real authenticated call works."""

    def calendarList(self):  # noqa: N802, ANN201
        class _CalList:
            def list(self_inner, **kwargs):  # noqa: ANN001, ANN202
                class _Req:
                    def execute(self_req):  # noqa: ANN202
                        return {"items": [{"id": "primary"}]}

                return _Req()

        return _CalList()


class _FailingProbeService:
    """calendarList().list() raises — the API is failing."""

    def calendarList(self):  # noqa: N802, ANN201
        class _CalList:
            def list(self_inner, **kwargs):  # noqa: ANN001, ANN202
                class _Req:
                    def execute(self_req):  # noqa: ANN202
                        raise RuntimeError("401 invalid_grant")

                return _Req()

        return _CalList()


@pytest.mark.asyncio
async def test_health_not_ok_when_connected_but_never_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token exists but the client can't be built → degraded, NOT ok."""
    cal = _calendar(tmp_path)
    monkeypatch.setattr(cal, "is_connected", lambda: _async_true())
    monkeypatch.setattr(cal, "_build_service", lambda: None)  # client unavailable

    status = await cal.health_check()
    assert status.status != "ok", "must not report ok without a verified API call"
    assert status.status == "degraded"


@pytest.mark.asyncio
async def test_health_ok_only_after_real_probe_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cal = _calendar(tmp_path)
    monkeypatch.setattr(cal, "is_connected", lambda: _async_true())
    monkeypatch.setattr(cal, "_build_service", lambda: _OkProbeService())

    status = await cal.health_check()
    assert status.status == "ok"


@pytest.mark.asyncio
async def test_health_degraded_when_probe_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cal = _calendar(tmp_path)
    monkeypatch.setattr(cal, "is_connected", lambda: _async_true())
    monkeypatch.setattr(cal, "_build_service", lambda: _FailingProbeService())

    status = await cal.health_check()
    assert status.status == "degraded"
    assert status.message  # an honest reason


@pytest.mark.asyncio
async def test_health_down_when_not_connected(tmp_path: Path) -> None:
    cal = _calendar(tmp_path)
    status = await cal.health_check()
    assert status.status == "down"

"""Tests for Stories 11.2 (Gmail) and 11.3 (Calendar) — 15 test cases.

Group 1: OAuthManager (5 tests)
Group 2: GmailAdapter  (5 tests)
Group 3: GoogleCalendarAdapter (5 tests)

All tests use tmp_path for filesystem isolation and never import google-auth-oauthlib
directly — only the pure adapter logic is tested.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from stackowl.integrations.oauth_manager import OAuthManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MASTER_KEY = "test-key-123"


def _make_oauth(tmp_path: Path, service: str = "gmail") -> OAuthManager:
    return OAuthManager(
        service_name=service,
        credentials_dir=tmp_path / "creds",
        master_key=_MASTER_KEY,
    )


def _sample_token() -> dict[str, object]:
    return {
        "token": "access-token-abc",
        "refresh_token": "refresh-token-xyz",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        "expiry": None,
    }


# ---------------------------------------------------------------------------
# Group 1: OAuthManager
# ---------------------------------------------------------------------------


def test_oauth_manager_save_and_load(tmp_path: Path) -> None:
    """save() then load() returns the same token data."""
    om = _make_oauth(tmp_path)
    original = _sample_token()
    om.save(original)
    loaded = om.load()
    assert loaded is not None
    assert loaded["token"] == original["token"]
    assert loaded["refresh_token"] == original["refresh_token"]
    assert loaded["scopes"] == original["scopes"]


def test_oauth_manager_load_nonexistent(tmp_path: Path) -> None:
    """load() returns None when no credentials file exists."""
    om = _make_oauth(tmp_path)
    result = om.load()
    assert result is None


def test_oauth_manager_delete_removes_file(tmp_path: Path) -> None:
    """delete() removes the file and exists() returns False afterwards."""
    om = _make_oauth(tmp_path)
    om.save(_sample_token())
    assert om.exists()
    om.delete()
    assert not om.exists()
    creds_dir = tmp_path / "creds"
    assert not (creds_dir / "gmail.enc").exists()


def test_oauth_manager_save_encrypts_file(tmp_path: Path) -> None:
    """The stored file is not plain JSON — it is base64-encoded ciphertext."""
    om = _make_oauth(tmp_path)
    om.save(_sample_token())
    creds_file = tmp_path / "creds" / "gmail.enc"
    raw = creds_file.read_text(encoding="utf-8")
    # The raw content must NOT be parseable as JSON
    try:
        json.loads(raw)
        is_plain_json = True
    except (json.JSONDecodeError, ValueError):
        is_plain_json = False
    assert not is_plain_json, "Credentials file must not be stored as plain JSON"
    # It must be non-empty base64-like content
    assert len(raw) > 20


def test_oauth_manager_exists_returns_false_before_save(tmp_path: Path) -> None:
    """exists() returns False on a fresh OAuthManager with no prior saves."""
    om = _make_oauth(tmp_path)
    assert not om.exists()


# ---------------------------------------------------------------------------
# Group 2: GmailAdapter
# ---------------------------------------------------------------------------


def _make_gmail_adapter(tmp_path: Path, autonomy_level: str = "medium") -> object:
    from stackowl.integrations.gmail import GmailAdapter

    om = _make_oauth(tmp_path, "gmail")
    return GmailAdapter(
        client_id="fake-client-id",
        client_secret="fake-client-secret",
        oauth_manager=om,
        autonomy_level=autonomy_level,
    )


def test_gmail_adapter_service_name(tmp_path: Path) -> None:
    """service_name is 'gmail'."""
    from stackowl.integrations.gmail import GmailAdapter

    adapter = _make_gmail_adapter(tmp_path)
    assert adapter.service_name == "gmail"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_gmail_adapter_is_connected_false_without_creds(tmp_path: Path) -> None:
    """is_connected() is False when no credentials file exists."""
    adapter = _make_gmail_adapter(tmp_path)
    assert not await adapter.is_connected()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_gmail_adapter_is_connected_true_with_creds(tmp_path: Path) -> None:
    """is_connected() is True when a credentials file exists."""
    from stackowl.integrations.gmail import GmailAdapter

    om = _make_oauth(tmp_path, "gmail")
    om.save(_sample_token())
    adapter = GmailAdapter(
        client_id="id",
        client_secret="secret",
        oauth_manager=om,
    )
    assert await adapter.is_connected()


@pytest.mark.asyncio
async def test_gmail_adapter_unsupported_action_raises(tmp_path: Path) -> None:
    """execute_action with an unknown action raises UnsupportedActionError."""
    from stackowl.exceptions import UnsupportedActionError

    adapter = _make_gmail_adapter(tmp_path)
    with pytest.raises(UnsupportedActionError) as exc_info:
        await adapter.execute_action("delete_all", {})  # type: ignore[union-attr]
    assert exc_info.value.service_name == "gmail"
    assert exc_info.value.action == "delete_all"


@pytest.mark.asyncio
async def test_gmail_adapter_send_email_requires_confirmation_at_medium_autonomy(
    tmp_path: Path,
) -> None:
    """send_email at medium autonomy returns requires_confirmation status."""
    adapter = _make_gmail_adapter(tmp_path, autonomy_level="medium")
    result = await adapter.execute_action(  # type: ignore[union-attr]
        "send_email",
        {"to": "user@example.com", "subject": "Hello"},
    )
    assert result.status == "requires_confirmation"
    assert result.confirmation_prompt != ""


# ---------------------------------------------------------------------------
# Group 3: GoogleCalendarAdapter
# ---------------------------------------------------------------------------


def _make_calendar_adapter(tmp_path: Path, autonomy_level: str = "medium") -> object:
    from stackowl.integrations.google_calendar import GoogleCalendarAdapter

    om = _make_oauth(tmp_path, "google_calendar")
    return GoogleCalendarAdapter(
        client_id="fake-client-id",
        client_secret="fake-client-secret",
        oauth_manager=om,
        autonomy_level=autonomy_level,
    )


def test_calendar_adapter_service_name(tmp_path: Path) -> None:
    """service_name is 'google_calendar'."""
    adapter = _make_calendar_adapter(tmp_path)
    assert adapter.service_name == "google_calendar"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_calendar_adapter_is_connected_false(tmp_path: Path) -> None:
    """is_connected() is False when no credentials file exists."""
    adapter = _make_calendar_adapter(tmp_path)
    assert not await adapter.is_connected()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_calendar_adapter_is_connected_true_with_creds(tmp_path: Path) -> None:
    """is_connected() is True after saving credentials into the OAuthManager."""
    from stackowl.integrations.google_calendar import GoogleCalendarAdapter

    om = _make_oauth(tmp_path, "google_calendar")
    om.save(_sample_token())
    adapter = GoogleCalendarAdapter(
        client_id="id",
        client_secret="secret",
        oauth_manager=om,
    )
    assert await adapter.is_connected()


@pytest.mark.asyncio
async def test_calendar_adapter_unsupported_action_raises(tmp_path: Path) -> None:
    """execute_action with an unknown action raises UnsupportedActionError."""
    from stackowl.exceptions import UnsupportedActionError

    adapter = _make_calendar_adapter(tmp_path)
    with pytest.raises(UnsupportedActionError) as exc_info:
        await adapter.execute_action("delete_calendar", {})  # type: ignore[union-attr]
    assert exc_info.value.service_name == "google_calendar"
    assert exc_info.value.action == "delete_calendar"


@pytest.mark.asyncio
async def test_calendar_adapter_create_event_requires_confirmation_at_medium_autonomy(
    tmp_path: Path,
) -> None:
    """create_event at medium autonomy returns requires_confirmation status."""
    adapter = _make_calendar_adapter(tmp_path, autonomy_level="medium")
    result = await adapter.execute_action(  # type: ignore[union-attr]
        "create_event",
        {"title": "Team standup", "start": "2026-05-25T09:00:00Z"},
    )
    assert result.status == "requires_confirmation"
    assert result.confirmation_prompt != ""

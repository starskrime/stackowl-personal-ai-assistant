"""IntegrationAdapter contract tests — every registered adapter must pass."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

import pytest

from tests.spikes._reference_adapter import ReferenceAdapter


SERVICE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _all_adapter_classes() -> list[type]:
    from stackowl.integrations.gmail import GmailAdapter
    from stackowl.integrations.google_calendar import GoogleCalendarAdapter

    return [ReferenceAdapter, GmailAdapter, GoogleCalendarAdapter]


def _make_adapter(cls: type) -> Any:
    """Construct an adapter instance, providing required constructor args where needed."""
    from stackowl.integrations.gmail import GmailAdapter
    from stackowl.integrations.google_calendar import GoogleCalendarAdapter
    from stackowl.integrations.oauth_manager import OAuthManager

    if cls is GmailAdapter:
        td = tempfile.mkdtemp()
        mgr = OAuthManager("gmail", Path(td), "test-key")
        return GmailAdapter("cid", "csecret", mgr)
    if cls is GoogleCalendarAdapter:
        td = tempfile.mkdtemp()
        mgr = OAuthManager("google_calendar", Path(td), "test-key")
        return GoogleCalendarAdapter("cid", "csecret", mgr)
    return cls()


@pytest.mark.parametrize("cls", _all_adapter_classes())
def test_service_name_is_lowercase_alphanumeric(cls: type) -> None:
    adapter = _make_adapter(cls)
    assert SERVICE_NAME_RE.match(adapter.service_name), f"Invalid service_name: {adapter.service_name!r}"


@pytest.mark.parametrize("cls", _all_adapter_classes())
def test_has_health_contributor_methods(cls: type) -> None:
    adapter = _make_adapter(cls)
    assert hasattr(adapter, "contributor_name")
    assert hasattr(adapter, "health_check")


@pytest.mark.parametrize("cls", _all_adapter_classes())
@pytest.mark.asyncio
async def test_unsupported_action_raises(cls: type) -> None:
    from stackowl.exceptions import UnsupportedActionError

    adapter = _make_adapter(cls)
    with pytest.raises(UnsupportedActionError):
        await adapter.execute_action("__nonexistent_action__", {})


@pytest.mark.parametrize("cls", _all_adapter_classes())
@pytest.mark.asyncio
async def test_health_check_returns_health_status(cls: type) -> None:
    from stackowl.health.status import HealthStatus

    adapter = _make_adapter(cls)
    result = await adapter.health_check()
    assert isinstance(result, HealthStatus)
    assert result.name.startswith("integration.")

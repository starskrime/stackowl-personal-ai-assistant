"""Dispatch tests — /connect and /disconnect commands (Epic B, Commit 3).

Drives CommandRegistry.dispatch() through register_all_commands() with a
fake IntegrationRegistry holding a fake adapter.

Key assertions:
  /connect  — lists adapters (reachable)
  /disconnect <service> — calls delete_credentials() via public method;
                          reports "credentials removed" when True,
                          honest "(no stored credentials)" when False
  /connect + /disconnect — honest not-configured when registry is None
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from stackowl.brief.models import BriefSection
from stackowl.health.status import HealthStatus
from stackowl.integrations.base import ActionResult, IntegrationAdapter
from stackowl.integrations.registry import IntegrationRegistry
from tests._story_6_7_helpers import make_state


# ---------------------------------------------------------------------------
# Fake adapter
# ---------------------------------------------------------------------------

class FakeAdapter(IntegrationAdapter):
    """Minimal adapter for command dispatch tests."""

    def __init__(self, name: str, *, has_creds: bool = True) -> None:
        self._name = name
        self._has_creds = has_creds
        self.delete_credentials_calls: int = 0

    @property
    def service_name(self) -> str:
        return self._name

    async def connect(self) -> None:
        pass

    async def is_connected(self) -> bool:
        return self._has_creds

    async def refresh_credentials(self) -> None:
        pass

    async def get_morning_brief_section(self) -> BriefSection | None:
        return None

    async def execute_action(self, action: str, params: dict[str, Any]) -> ActionResult:
        return ActionResult(status="ok")

    async def delete_credentials(self) -> bool:
        self.delete_credentials_calls += 1
        had = self._has_creds
        self._has_creds = False
        return had


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()
    IntegrationRegistry.reset()


@pytest.fixture()
def adapter_with_creds() -> FakeAdapter:
    return FakeAdapter("fake-service", has_creds=True)


@pytest.fixture()
def adapter_no_creds() -> FakeAdapter:
    return FakeAdapter("empty-service", has_creds=False)


@pytest.fixture()
def int_registry(adapter_with_creds: FakeAdapter, adapter_no_creds: FakeAdapter) -> IntegrationRegistry:
    reg = IntegrationRegistry()
    reg.register(adapter_with_creds)
    reg.register(adapter_no_creds)
    return reg


@pytest.fixture()
def reg(int_registry: IntegrationRegistry) -> CommandRegistry:
    deps = CommandDeps(integration_registry=int_registry)
    return register_all_commands(deps, registry=CommandRegistry.instance())


# ---------------------------------------------------------------------------
# /connect tests
# ---------------------------------------------------------------------------

async def test_connect_list_shows_registered_adapters(
    reg: CommandRegistry,
) -> None:
    """dispatch 'connect' with no args → lists all registered integrations."""
    state = make_state()
    result = (await reg.dispatch("connect", "", state)).text

    assert "fake-service" in result
    assert "empty-service" in result


async def test_connect_not_configured_when_registry_none() -> None:
    """dispatch 'connect' with no registry → honest not-configured."""
    CommandRegistry.reset()
    deps = CommandDeps(integration_registry=None)
    reg = register_all_commands(deps, registry=CommandRegistry.instance())
    state = make_state()

    result = (await reg.dispatch("connect", "", state)).text

    assert "not configured" in result.lower() or "✗" in result


# ---------------------------------------------------------------------------
# /disconnect tests
# ---------------------------------------------------------------------------

async def test_disconnect_with_creds_reports_removed(
    reg: CommandRegistry, adapter_with_creds: FakeAdapter
) -> None:
    """dispatch 'disconnect fake-service' → calls delete_credentials(); reports removed."""
    state = make_state()
    result = (await reg.dispatch("disconnect", "fake-service", state)).text

    assert adapter_with_creds.delete_credentials_calls == 1
    assert "credentials removed" in result.lower()
    assert "✓" in result


async def test_disconnect_no_creds_reports_honest_nothing_to_remove(
    reg: CommandRegistry, adapter_no_creds: FakeAdapter
) -> None:
    """dispatch 'disconnect empty-service' → delete_credentials() returns False → honest message."""
    state = make_state()
    result = (await reg.dispatch("disconnect", "empty-service", state)).text

    assert adapter_no_creds.delete_credentials_calls == 1
    # Must NOT claim "credentials removed" when none existed
    assert "no stored credentials" in result.lower()
    assert "✓" in result


async def test_disconnect_unknown_service_returns_not_found(
    reg: CommandRegistry,
) -> None:
    """dispatch 'disconnect bogus' → honest not-found."""
    state = make_state()
    result = (await reg.dispatch("disconnect", "bogus-service", state)).text

    assert "unknown" in result.lower() or "not found" in result.lower()


async def test_disconnect_not_configured_when_registry_none() -> None:
    """dispatch 'disconnect foo' with no registry → honest not-configured."""
    CommandRegistry.reset()
    deps = CommandDeps(integration_registry=None)
    reg = register_all_commands(deps, registry=CommandRegistry.instance())
    state = make_state()

    result = (await reg.dispatch("disconnect", "foo", state)).text

    assert "not configured" in result.lower() or "✗" in result

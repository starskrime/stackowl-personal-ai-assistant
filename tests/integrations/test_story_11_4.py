"""Tests for Story 11.4 — IntegrationSectionAssembler, DisconnectCommand, MorningBriefHandler wiring.

Group 1: IntegrationSectionAssembler (5 tests)
Group 2: DisconnectCommand (4 tests)
Group 3: Morning brief + integrations wiring (3 tests)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Group 1: IntegrationSectionAssembler
# ---------------------------------------------------------------------------


def _make_mock_registry(adapters: list[Any]) -> MagicMock:
    """Return a mock IntegrationRegistry whose list_connected returns adapters."""
    registry = MagicMock()
    registry.list_connected = AsyncMock(return_value=adapters)
    return registry


def _make_mock_adapter(service_name: str, section_items: list[str] | None = None) -> MagicMock:
    """Return a mock adapter that returns a BriefSection with given items."""
    from stackowl.brief.models import BriefSection

    adapter = MagicMock()
    adapter.service_name = service_name
    if section_items is not None:
        adapter.get_morning_brief_section = AsyncMock(
            return_value=BriefSection(
                key=service_name,
                title=service_name.capitalize(),
                items=section_items,
            )
        )
    else:
        adapter.get_morning_brief_section = AsyncMock(return_value=None)
    return adapter


def _make_brief_ctx() -> MagicMock:
    """Return a minimal mock BriefContext."""
    ctx = MagicMock()
    ctx.job_id = "job-test-001"
    return ctx


@pytest.mark.asyncio
async def test_assembler_no_adapters_returns_omitted_section() -> None:
    """Empty registry → returned section has omitted=True."""
    from stackowl.integrations.integration_assembler import IntegrationSectionAssembler

    registry = _make_mock_registry([])
    assembler = IntegrationSectionAssembler(registry)
    ctx = _make_brief_ctx()

    section = await assembler.assemble(ctx)

    assert section.omitted is True
    assert section.key == "integrations"
    assert section.items == []


@pytest.mark.asyncio
async def test_assembler_one_connected_adapter_adds_items() -> None:
    """A connected adapter with items → items appear in assembled section."""
    from stackowl.integrations.integration_assembler import IntegrationSectionAssembler

    adapter = _make_mock_adapter("gmail", ["email 1", "email 2"])
    registry = _make_mock_registry([adapter])
    assembler = IntegrationSectionAssembler(registry)
    ctx = _make_brief_ctx()

    section = await assembler.assemble(ctx)

    assert section.omitted is False
    assert "email 1" in section.items
    assert "email 2" in section.items


@pytest.mark.asyncio
async def test_assembler_timeout_adds_timeout_message() -> None:
    """Adapter that raises asyncio.TimeoutError → section contains timed out message."""
    from stackowl.integrations.integration_assembler import IntegrationSectionAssembler

    async def _slow(*a: Any, **kw: Any) -> None:
        raise asyncio.TimeoutError()

    adapter = _make_mock_adapter("gmail")
    adapter.get_morning_brief_section = _slow  # type: ignore[method-assign]

    registry = _make_mock_registry([adapter])

    # Patch _TIMEOUT_SECONDS to minimal value so wait_for triggers quickly
    with patch("stackowl.integrations.integration_assembler._TIMEOUT_SECONDS", 0.01):
        assembler = IntegrationSectionAssembler(registry)
        ctx = _make_brief_ctx()
        section = await assembler.assemble(ctx)

    assert any("timed out" in item for item in section.items), (
        f"Expected 'timed out' message in items, got: {section.items}"
    )


@pytest.mark.asyncio
async def test_assembler_adapter_exception_is_caught() -> None:
    """Adapter that raises RuntimeError → no exception propagated; other items still collected."""
    from stackowl.integrations.integration_assembler import IntegrationSectionAssembler
    from stackowl.brief.models import BriefSection

    bad_adapter = _make_mock_adapter("gmail")
    bad_adapter.get_morning_brief_section = AsyncMock(side_effect=RuntimeError("boom"))

    good_adapter = _make_mock_adapter("calendar", ["meeting at 9am"])

    registry = _make_mock_registry([bad_adapter, good_adapter])
    assembler = IntegrationSectionAssembler(registry)
    ctx = _make_brief_ctx()

    # Must not raise
    section = await assembler.assemble(ctx)

    assert "meeting at 9am" in section.items


@pytest.mark.asyncio
async def test_assembler_none_section_is_skipped() -> None:
    """Adapter returns None → no items added, section still returned without error."""
    from stackowl.integrations.integration_assembler import IntegrationSectionAssembler

    adapter = _make_mock_adapter("gmail", section_items=None)
    # _make_mock_adapter with None already sets get_morning_brief_section to return None
    registry = _make_mock_registry([adapter])
    assembler = IntegrationSectionAssembler(registry)
    ctx = _make_brief_ctx()

    section = await assembler.assemble(ctx)

    assert section.items == []
    assert section.omitted is True


# ---------------------------------------------------------------------------
# Group 2: DisconnectCommand
# ---------------------------------------------------------------------------


def _make_disconnect_command(adapters: dict[str, Any] | None = None) -> Any:
    """Return a DisconnectCommand wired to a registry with the given adapters."""
    from stackowl.commands.connect_command import DisconnectCommand
    from stackowl.integrations.registry import IntegrationRegistry
    from stackowl.exceptions import IntegrationNotFoundError

    registry = MagicMock(spec=IntegrationRegistry)
    if adapters:
        def _get(service_name: str) -> Any:
            if service_name in adapters:
                return adapters[service_name]
            raise IntegrationNotFoundError(service_name)
        registry.get.side_effect = _get
    else:
        registry.get.side_effect = IntegrationNotFoundError("unknown")

    return DisconnectCommand(integration_registry=registry)


def _make_pipeline_state() -> MagicMock:
    state = MagicMock()
    state.session_id = "sess-test"
    return state


def test_disconnect_command_name() -> None:
    """command property returns 'disconnect'."""
    cmd = _make_disconnect_command()
    assert cmd.command == "disconnect"


@pytest.mark.asyncio
async def test_disconnect_command_no_args_returns_usage() -> None:
    """handle('') returns a usage message."""
    cmd = _make_disconnect_command()
    result = await cmd.handle("", _make_pipeline_state())
    assert "Usage" in result


@pytest.mark.asyncio
async def test_disconnect_command_unknown_service() -> None:
    """handle with unknown service name returns 'Unknown integration'."""
    cmd = _make_disconnect_command()
    result = await cmd.handle("no_such_service", _make_pipeline_state())
    assert "Unknown integration" in result


@pytest.mark.asyncio
async def test_disconnect_command_known_service_calls_oauth_delete() -> None:
    """handle with a known service calls _oauth.delete() on the adapter."""
    oauth_mock = MagicMock()
    oauth_mock.delete = MagicMock()

    adapter = MagicMock()
    adapter._oauth = oauth_mock
    # adapter has no 'disconnect' method to avoid async issues
    del adapter.disconnect  # remove it so hasattr returns False

    cmd = _make_disconnect_command({"gmail": adapter})
    result = await cmd.handle("gmail", _make_pipeline_state())

    oauth_mock.delete.assert_called_once()
    assert "gmail" in result
    assert "disconnected" in result.lower() or "✓" in result


# ---------------------------------------------------------------------------
# Group 3: Morning brief + integrations wiring
# ---------------------------------------------------------------------------


def _make_morning_brief_handler(integration_registry: Any = None) -> Any:
    """Construct MorningBriefHandler with mock deps. Avoids calling execute()."""
    from stackowl.scheduler.handlers.morning_brief import MorningBriefHandler
    from stackowl.config.settings import Settings

    memory_bridge = MagicMock()
    scheduler = MagicMock()
    db = MagicMock()
    event_bus = MagicMock()
    settings = MagicMock(spec=Settings)
    # brief.sections and brief.channels are accessed in execute(), not __init__
    settings.brief = MagicMock()
    settings.brief.sections = {}
    settings.brief.channels = []

    return MorningBriefHandler(
        memory_bridge=memory_bridge,
        scheduler=scheduler,
        db=db,
        event_bus=event_bus,
        settings=settings,
        integration_registry=integration_registry,
    )


def test_morning_brief_handler_with_no_integration_registry() -> None:
    """MorningBriefHandler with integration_registry=None has exactly 4 assemblers."""
    handler = _make_morning_brief_handler(integration_registry=None)
    assert len(handler._assemblers) == 4


def test_morning_brief_handler_with_integration_registry() -> None:
    """MorningBriefHandler with an integration_registry has exactly 5 assemblers."""
    from stackowl.integrations.registry import IntegrationRegistry

    registry = MagicMock(spec=IntegrationRegistry)
    handler = _make_morning_brief_handler(integration_registry=registry)
    assert len(handler._assemblers) == 5

    # The 5th assembler should be IntegrationSectionAssembler
    from stackowl.integrations.integration_assembler import IntegrationSectionAssembler
    assert isinstance(handler._assemblers[-1], IntegrationSectionAssembler)


def test_integration_registry_singleton_reset() -> None:
    """IntegrationRegistry.reset() clears the singleton so next instance() is fresh."""
    from stackowl.integrations.registry import IntegrationRegistry

    # Get an instance and register a mock adapter
    r1 = IntegrationRegistry.instance()
    mock_adapter = MagicMock()
    mock_adapter.service_name = "test_service"
    r1.register(mock_adapter)

    # Reset
    IntegrationRegistry.reset()

    # New instance should be empty
    r2 = IntegrationRegistry.instance()
    assert r2 is not r1
    assert r2.list_all() == []

    # Cleanup
    IntegrationRegistry.reset()

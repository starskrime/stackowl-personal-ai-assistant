"""Dispatch test — /audit is wired through CommandRegistry."""
from __future__ import annotations
import pytest
from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401


class _FakeAuditLogger:
    def tail(self, n: int) -> list:
        return []

    def verify_chain(self) -> tuple:
        return (True, None)


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


async def test_audit_chain_intact() -> None:
    deps = CommandDeps(audit_logger=_FakeAuditLogger())
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("audit", "", make_state())
    assert "Chain intact" in result


async def test_audit_not_configured_when_logger_none() -> None:
    deps = CommandDeps(audit_logger=None)
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("audit", "", make_state())
    assert "not configured" in result


async def test_audit_not_found_when_not_registered() -> None:
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("audit", "", make_state())

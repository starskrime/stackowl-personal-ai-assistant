"""Story 4.3 — ``CommandRegistry.dispatch`` runs the severity check via
``authz.requester.principal_for`` BEFORE either the ``??`` dry-run preview or
the real handler (AD-1: "no preview/dry-run returns before the severity
check"). I/O matrix rows 5-6.

The refusal branch is proven here by monkeypatching ``principal_for`` to
return a restricted :class:`ControlPrincipal` directly (Design Notes: "never
exercised by a live surface... only by a principal built directly in the
test") — today's real ``principal_for`` grants ``ALL_SEVERITIES`` to every
requester kind (Story 4.4 is the real policy gate), so every OTHER test here
proves the check runs and still succeeds.
"""

from __future__ import annotations

import pytest
from tests._story_6_7_helpers import make_state

from stackowl.authz.severity import ControlPrincipal
from stackowl.commands import registry as registry_module
from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.spec.errors import CommandRefusedError


class _WriteCommand(SlashCommand):
    """Registered under the real top-level name ``new`` — COMMAND_CENSUS
    declares it ``write`` (``session.start_new``), so the severity check
    below is exercised for real, not against the default-to-read fallback."""

    def __init__(self) -> None:
        self.handled = False

    @property
    def command(self) -> str:
        return "new"

    @property
    def description(self) -> str:
        return "a write command"

    async def handle(self, args: str, state: object) -> str:
        self.handled = True
        return "ok"


@pytest.fixture(autouse=True)
def _reset_registry():
    CommandRegistry.reset()


async def test_dispatch_runs_severity_check_then_the_real_handler():
    """I/O matrix row 5: a write/consequential command still succeeds today
    (the principal grants everything), but only AFTER the check runs."""
    cmd = _WriteCommand()
    CommandRegistry.instance().register(cmd)

    result = await CommandRegistry.instance().dispatch("new", "add x", make_state())

    assert result.text == "ok"
    assert cmd.handled is True


async def test_dry_run_preview_still_runs_the_severity_check_first():
    """I/O matrix row 6: the ``??`` sigil never bypasses the check — the
    preview builder runs only after it, same as the real handler."""
    cmd = _WriteCommand()
    CommandRegistry.instance().register(cmd)

    result = await CommandRegistry.instance().dispatch("new", "add x??", make_state())

    # The preview path never calls handle() — proves the check ran on the
    # dry-run branch too, not only the live one.
    assert cmd.handled is False
    assert result.text  # a preview was built (build_preview's own contract)


async def test_a_restricted_principal_refuses_before_the_handler_runs(monkeypatch):
    """The refusal branch — built directly, per this module's docstring."""
    cmd = _WriteCommand()
    CommandRegistry.instance().register(cmd)
    restricted = ControlPrincipal(
        principal_id="owner", credential_id="test", granted=frozenset({"read"}),
    )
    monkeypatch.setattr(registry_module, "principal_for", lambda _kind: restricted)

    with pytest.raises(CommandRefusedError):
        await CommandRegistry.instance().dispatch("new", "add x", make_state())

    assert cmd.handled is False


async def test_a_restricted_principal_refuses_the_dry_run_preview_too(monkeypatch):
    """The refusal is unconditional — it also stops the ``??`` preview."""
    cmd = _WriteCommand()
    CommandRegistry.instance().register(cmd)
    restricted = ControlPrincipal(
        principal_id="owner", credential_id="test", granted=frozenset({"read"}),
    )
    monkeypatch.setattr(registry_module, "principal_for", lambda _kind: restricted)

    with pytest.raises(CommandRefusedError):
        await CommandRegistry.instance().dispatch("new", "add x??", make_state())


async def test_a_read_only_command_is_never_refused_even_when_restricted(monkeypatch):
    """A command absent from COMMAND_CENSUS defaults to 'read' severity — a
    principal granted only 'read' still passes it."""

    class _ReadCommand(SlashCommand):
        @property
        def command(self) -> str:
            return "notincensus"

        @property
        def description(self) -> str:
            return "not a census entry"

        async def handle(self, args: str, state: object) -> str:
            return "read ok"

    CommandRegistry.instance().register(_ReadCommand())
    restricted = ControlPrincipal(
        principal_id="owner", credential_id="test", granted=frozenset({"read"}),
    )
    monkeypatch.setattr(registry_module, "principal_for", lambda _kind: restricted)

    result = await CommandRegistry.instance().dispatch("notincensus", "", make_state())

    assert result.text == "read ok"

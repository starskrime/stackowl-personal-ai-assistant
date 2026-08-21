"""The operator's own console counts as an official origin.

ESC-30, Bakir 2026-08-21. Authority follows the ORIGIN of a request (0f1431e9), and
"official" means a channel the gateway holds a live adapter for. Measured 2026-08-21:
`ChannelRegistry` held exactly ONE adapter — `telegram`, 585 registrations — while the
console had produced 17 turns and registered **zero** times.

So an `owl_build` or `authority_widening` request typed at the operator's own terminal
was UNOFFICIAL, and prompted. That is the most attended surface there is, and prompting
there is what "never ask me to enable anything" exists to prevent.

THE CAUSE, and it is a wiring gap rather than a policy one. `orchestrator.py`
constructs the `CLIAdapter` and registers it with the CLARIFY gateway
(`clarify_gateway.register_adapter("cli", adapter)`) — but never with
`ChannelRegistry`, which is the registry provenance actually reads. Telegram has a
`register_with_registry()` and calls it; the console had no equivalent. The adapter was
live and reachable, and simply invisible to the one lookup that decides authority.

THE COUNTER-ARGUMENT, kept because it is real: the console is reachable by anything
running on this box, including an owl that holds shell. It is therefore a weaker origin
claim than an authenticated remote channel. Bakir weighed that and chose it; recorded so
the trade is visible if it ever matters.
"""

from __future__ import annotations

import inspect

import pytest

from stackowl.channels.registry import ChannelRegistry


class _ConsoleAdapter:
    """Stands in for CLIAdapter — only `channel_name` is read by provenance."""

    channel_name = "cli"

    async def send(self, *_a: object, **_k: object) -> None:  # pragma: no cover
        return None


@pytest.fixture()
def _clean_registry():
    registry = ChannelRegistry.instance()
    registry.reset()
    yield registry
    registry.reset()


class TestProvenanceSeesTheConsole:
    def test_a_registered_console_is_an_official_channel(self, _clean_registry) -> None:
        """THE MECHANISM. `_gateway_channels()` is what decides `official`."""
        from stackowl.tools.consent import _gateway_channels

        _clean_registry.register(_ConsoleAdapter(), source_name="t")  # type: ignore[arg-type]

        assert "cli" in _gateway_channels()

    def test_an_UNregistered_console_is_not_official(self, _clean_registry) -> None:
        """The state the platform was actually in: 17 console turns, 0 registrations.
        Pinned so the fix cannot be mistaken for something that always worked."""
        from stackowl.tools.consent import _gateway_channels

        assert "cli" not in _gateway_channels()

    def test_it_never_raises_when_the_registry_is_empty(self, _clean_registry) -> None:
        """An origin we cannot establish must be UNOFFICIAL, never an exception — the
        caller fails closed on the empty set."""
        from stackowl.tools.consent import _gateway_channels

        assert _gateway_channels() == frozenset()


class TestTheOrchestratorActuallyRegistersIt:
    """The KEY check cannot see an unwired construction site; this one can.

    The tests above prove that a registered console is official. They would all pass
    while production still registered nothing — which is exactly the state measured on
    2026-08-21, and the same shape as the D16.1 defect where a declared extension point
    had a registry slot nobody filled.
    """

    @staticmethod
    def test_the_cli_adapter_is_registered_with_the_channel_registry() -> None:
        from stackowl.startup import orchestrator

        src = inspect.getsource(orchestrator)
        start = src.index("adapter = CLIAdapter(")
        window = src[start:start + 1400]

        assert "ChannelRegistry" in window and "register" in window, (
            "the console adapter is constructed but never registered with "
            "ChannelRegistry — provenance reads THAT registry, so an authority "
            "request from the operator's own terminal would be judged unofficial"
        )

    @staticmethod
    def test_the_clarify_registration_is_still_there_too() -> None:
        """Two registries, two purposes. Clarify needs the adapter to deliver a
        question back; provenance needs it to judge an origin. Adding the second must
        not quietly replace the first."""
        from stackowl.startup import orchestrator

        src = inspect.getsource(orchestrator)

        assert 'clarify_gateway.register_adapter("cli", adapter)' in src

"""D16.1 — the seventh extension point: a plugin that OBSERVES the agent.

The contract is decided in docs/reference-mapping/designs/D16.1.md and every test
here is one of its invariants, written before the module existed.

WHY OBSERVE-ONLY. A hook returns ``None`` and its return value is discarded: no
veto, no argument rewriting, no tool substitution. A ``pre_tool_call`` veto is the
largest trust surface in the item — third-party code deciding whether the agent may
act — and D17 owns security. Zero plugins are installed, so inventing a control
surface for a population of zero is over-architecting. Veto is a v1 NON-GOAL.

THE INVARIANTS UNDER TEST (numbering follows the design):
  I3 — a failing hook never costs the turn
  I4 — a hook cannot hang the turn
  I5 — a repeatedly failing hook is DISABLED, not merely logged
  I6 — an ungranted hook does not run (enforced at LOAD; see the capability test)

And the property that lets this ship before any plugin exists: with nothing
registered, dispatch is a dict lookup and returns without touching an event loop
primitive. A hook surface that cost every tool call something would not be worth
having.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from stackowl.plugins.hooks import (
    HOOK_POINTS,
    POST_TOOL_CALL,
    PRE_TOOL_CALL,
    HookRegistry,
    LifecycleHook,
)


class _RecordingHook(LifecycleHook):
    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, object]]] = []

    async def pre_tool_call(self, event: object) -> None:
        self.seen.append((PRE_TOOL_CALL, dict(event)))  # type: ignore[arg-type]

    async def post_tool_call(self, event: object) -> None:
        self.seen.append((POST_TOOL_CALL, dict(event)))  # type: ignore[arg-type]


class _RaisingHook(LifecycleHook):
    def __init__(self) -> None:
        self.calls = 0

    async def pre_tool_call(self, event: object) -> None:
        self.calls += 1
        raise RuntimeError("this plugin is broken")


class _HangingHook(LifecycleHook):
    def __init__(self) -> None:
        self.entered = 0
        self.finished = 0

    async def pre_tool_call(self, event: object) -> None:
        self.entered += 1
        await asyncio.sleep(30)
        self.finished += 1


class _VetoingHook(LifecycleHook):
    """Tries to change the world by returning something. It cannot."""

    async def pre_tool_call(self, event: object) -> str:
        return "DENY"


class TestWhatAPluginImplementsIsWhatItRegisters:
    def test_only_the_overridden_points_are_armed(self) -> None:
        """Declared by construction. The design refuses a second list of hook
        points in the manifest, because two writers to one fact is how they
        drift — the shape this tree keeps paying for."""
        registry = HookRegistry()

        points = registry.register(_RecordingHook(), source_name="acme")

        assert set(points) == {PRE_TOOL_CALL, POST_TOOL_CALL}
        assert registry.has(PRE_TOOL_CALL)
        assert not registry.has("on_session_end")

    def test_a_hook_that_overrides_nothing_registers_nothing(self) -> None:
        """A LifecycleHook subclass with no overrides is a mistake, not a hook on
        every point. It arms nothing rather than being handed every event."""
        registry = HookRegistry()

        points = registry.register(LifecycleHook(), source_name="empty")

        assert points == ()
        assert all(not registry.has(p) for p in HOOK_POINTS)


class TestDispatchCostsNothingWhenNothingIsRegistered:
    @pytest.mark.asyncio
    async def test_an_empty_point_returns_without_work(self) -> None:
        registry = HookRegistry()

        await registry.dispatch(PRE_TOOL_CALL, {"tool": "shell"})

        assert not registry.has(PRE_TOOL_CALL)


class TestObserveOnly:
    @pytest.mark.asyncio
    async def test_the_hook_sees_the_event(self) -> None:
        registry = HookRegistry()
        hook = _RecordingHook()
        registry.register(hook, source_name="acme")

        await registry.dispatch(PRE_TOOL_CALL, {"tool": "shell", "arguments": {"cmd": "ls"}})

        assert hook.seen == [(PRE_TOOL_CALL, {"tool": "shell", "arguments": {"cmd": "ls"}})]

    @pytest.mark.asyncio
    async def test_a_returned_value_is_discarded(self) -> None:
        """Observe-only is the v1 boundary. dispatch() returns None whatever a
        hook says, so no caller can grow a veto by accident."""
        registry = HookRegistry()
        registry.register(_VetoingHook(), source_name="bossy")

        assert await registry.dispatch(PRE_TOOL_CALL, {"tool": "shell"}) is None

    @pytest.mark.asyncio
    async def test_a_hook_cannot_mutate_the_event_the_next_hook_sees(self) -> None:
        """The view is read-only. One plugin editing another's payload would be a
        control surface arriving through the back door."""
        registry = HookRegistry()

        class _Mutator(LifecycleHook):
            error: Exception | None = None

            async def pre_tool_call(self, event: object) -> None:
                try:
                    event["tool"] = "rm -rf"  # type: ignore[index]
                except Exception as exc:  # noqa: BLE001 — the point of the test
                    _Mutator.error = exc

        mutator = _Mutator()
        watcher = _RecordingHook()
        registry.register(mutator, source_name="mutator")
        registry.register(watcher, source_name="watcher")

        await registry.dispatch(PRE_TOOL_CALL, {"tool": "shell"})

        assert isinstance(_Mutator.error, TypeError)
        assert watcher.seen == [(PRE_TOOL_CALL, {"tool": "shell"})]


class TestAFailingHookNeverCostsTheTurn:
    @pytest.mark.asyncio
    async def test_a_raising_hook_is_swallowed_and_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """I3. The turn proceeds as if the hook were absent — and the ERROR names
        the plugin, because a hook that fails silently is a plugin nobody will
        ever fix."""
        registry = HookRegistry()
        registry.register(_RaisingHook(), source_name="broken-plugin")

        with caplog.at_level(logging.ERROR, logger="stackowl.plugins"):
            await registry.dispatch(PRE_TOOL_CALL, {"tool": "shell"})

        assert any(
            "hooks.dispatch" in r.message and "broken-plugin" in str(r.__dict__.get("_fields", ""))
            for r in caplog.records
        ), caplog.text

    @pytest.mark.asyncio
    async def test_one_broken_hook_does_not_stop_the_next(self) -> None:
        registry = HookRegistry()
        registry.register(_RaisingHook(), source_name="broken")
        watcher = _RecordingHook()
        registry.register(watcher, source_name="fine")

        await registry.dispatch(PRE_TOOL_CALL, {"tool": "shell"})

        assert watcher.seen


class TestAHookCannotHangTheTurn:
    @pytest.mark.asyncio
    async def test_a_hanging_hook_is_abandoned_not_awaited(self) -> None:
        """I4. Bounded by a timeout — the same reasoning as plugins/boot.py's
        load timeout: third-party code on a live path must never be able to hold
        the platform."""
        registry = HookRegistry(timeout_seconds=0.05)
        hook = _HangingHook()
        registry.register(hook, source_name="slow")

        await asyncio.wait_for(registry.dispatch(PRE_TOOL_CALL, {"tool": "shell"}), timeout=5)

        assert hook.entered == 1
        assert hook.finished == 0


class TestARepeatedlyFailingHookIsDisabled:
    @pytest.mark.asyncio
    async def test_it_stops_being_called_and_the_operator_is_told(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """I5, and it is the standing self-healing rule rather than a nicety: if
        it degrades, something must NOTICE and ACT. Logging the same ERROR on
        every tool call for the life of the process is not acting."""
        registry = HookRegistry(max_consecutive_failures=3)
        hook = _RaisingHook()
        registry.register(hook, source_name="broken")

        with caplog.at_level(logging.WARNING, logger="stackowl.plugins"):
            for _ in range(5):
                await registry.dispatch(PRE_TOOL_CALL, {"tool": "shell"})

        assert hook.calls == 3, "the hook kept being called after it was disabled"
        assert not registry.has(PRE_TOOL_CALL), "a disabled hook still leaves its point armed"
        assert any("hooks.disable" in r.message for r in caplog.records), caplog.text

    @pytest.mark.asyncio
    async def test_a_hook_that_recovers_keeps_its_place(self) -> None:
        """CONSECUTIVE failures, not cumulative. A hook that fails once an hour is
        annoying; a hook that fails every call is broken. Only the second is
        disarmed, or a long-lived process would eventually disable everything."""
        registry = HookRegistry(max_consecutive_failures=2)

        class _Flaky(LifecycleHook):
            def __init__(self) -> None:
                self.calls = 0

            async def pre_tool_call(self, event: object) -> None:
                self.calls += 1
                if self.calls % 2 == 1:
                    raise RuntimeError("intermittent")

        hook = _Flaky()
        registry.register(hook, source_name="flaky")

        for _ in range(6):
            await registry.dispatch(PRE_TOOL_CALL, {"tool": "shell"})

        assert hook.calls == 6
        assert registry.has(PRE_TOOL_CALL)


class TestUnload:
    @pytest.mark.asyncio
    async def test_unregistering_a_source_drops_its_hooks(self) -> None:
        """Mirrors how the loader drops a plugin's classes. A hook surviving its
        plugin's removal is third-party code running after it was uninstalled."""
        registry = HookRegistry()
        hook = _RecordingHook()
        registry.register(hook, source_name="acme")

        registry.unregister_source("acme")
        await registry.dispatch(PRE_TOOL_CALL, {"tool": "shell"})

        assert hook.seen == []
        assert not registry.has(PRE_TOOL_CALL)

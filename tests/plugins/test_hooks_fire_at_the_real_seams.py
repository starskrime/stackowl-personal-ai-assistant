"""D16.1 — the hooks fire where the platform actually works, not where it is convenient.

THIS IS THE TEST THE ITEM EXISTS FOR. D16.1's own architect finding was that
NOTHING IN THE RUNNING PLATFORM EVER LOADED A PLUGIN: boot built the catalogue and
the registry, and the only thing that imports a plugin module had zero construction
sites. A hook registry with no dispatch site would be the same defect one layer up —
a mechanism declared in code and wired to nothing, reading as if it worked.

So each test below drives the REAL seam and asserts the hook saw it:

  pre/post_tool_call  -> ``Tool.__call__``, the wrapper every tool invocation goes
                         through (it is what times the call and wraps a raise into a
                         failed ToolResult).
  pre/post_llm_call   -> ``ModelProvider._resilient_round``, the shared bracket the
                         concrete providers wrap EVERY remote round in. NOT
                         ``LLMGateway``, whose docstring claims every LLM consumer
                         goes through it — measured 2026-08-19, roughly twenty call
                         sites call ``provider.complete``/``complete_with_tools``
                         directly, so a hook there would be an actuator wired on
                         some paths only, which is the first failure shape in
                         PROCESS.md.
  on_session_start/end -> ``SessionStore.resolve_for`` through the real ingress
                         path, so a boundary the platform recognises is the same
                         boundary a hook is told about.

Every test also proves the DEFAULT case matters: with no hook registered the seam
must behave exactly as before, because a platform with zero plugins installed is
the only configuration that exists today.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from stackowl.config.settings import SessionSettings
from stackowl.db.pool import DbPool
from stackowl.plugins.hooks import (
    ON_SESSION_END,
    ON_SESSION_START,
    POST_LLM_CALL,
    POST_TOOL_CALL,
    PRE_LLM_CALL,
    PRE_TOOL_CALL,
    HookRegistry,
    LifecycleHook,
)
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.sessions.ingress import resolve_turn_session
from stackowl.sessions.store import SessionStore
from stackowl.tools.base import Tool, ToolResult


class _Watcher(LifecycleHook):
    """Observes every point and remembers what it saw."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def on_session_start(self, event: Mapping[str, Any]) -> None:
        self.events.append((ON_SESSION_START, dict(event)))

    async def on_session_end(self, event: Mapping[str, Any]) -> None:
        self.events.append((ON_SESSION_END, dict(event)))

    async def pre_tool_call(self, event: Mapping[str, Any]) -> None:
        self.events.append((PRE_TOOL_CALL, dict(event)))

    async def post_tool_call(self, event: Mapping[str, Any]) -> None:
        self.events.append((POST_TOOL_CALL, dict(event)))

    async def pre_llm_call(self, event: Mapping[str, Any]) -> None:
        self.events.append((PRE_LLM_CALL, dict(event)))

    async def post_llm_call(self, event: Mapping[str, Any]) -> None:
        self.events.append((POST_LLM_CALL, dict(event)))

    def points(self) -> list[str]:
        return [p for p, _ in self.events]

    def payload(self, point: str) -> dict[str, Any]:
        for p, e in self.events:
            if p == point:
                return e
        raise AssertionError(f"{point} never fired — saw {self.points()}")


@pytest.fixture
def watcher() -> Iterator[_Watcher]:
    """A hook armed on the PROCESS-WIDE registry, restored afterwards.

    The seams reach the singleton, because a call site threading a registry
    through every layer to reach a tool call is plumbing nobody would keep.
    """
    previous = HookRegistry._instance  # noqa: SLF001
    HookRegistry._instance = HookRegistry(timeout_seconds=5.0)  # noqa: SLF001
    hook = _Watcher()
    HookRegistry.instance().register(hook, source_name="test-observer")
    try:
        yield hook
    finally:
        HookRegistry._instance = previous  # noqa: SLF001


# --------------------------------------------------------------------- tools


class _ProbeTool(Tool):
    @property
    def name(self) -> str:
        return "probe"

    @property
    def description(self) -> str:
        return "A tool that exists only to be observed by a lifecycle hook."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="done", duration_ms=0.0)


class _RaisingTool(_ProbeTool):
    async def execute(self, **kwargs: object) -> ToolResult:
        raise RuntimeError("the tool blew up")


class TestTheToolSeam:
    async def test_both_points_fire_around_a_real_tool_call(self, watcher: _Watcher) -> None:
        result = await _ProbeTool()(argument="value")

        assert result.success
        assert watcher.points() == [PRE_TOOL_CALL, POST_TOOL_CALL]
        assert watcher.payload(PRE_TOOL_CALL)["tool"] == "probe"
        assert watcher.payload(PRE_TOOL_CALL)["arguments"] == {"argument": "value"}
        post = watcher.payload(POST_TOOL_CALL)
        assert post["success"] is True
        assert post["duration_ms"] is not None

    async def test_a_failing_tool_still_reports_post(self, watcher: _Watcher) -> None:
        """A failure is the event an observer most wants. Firing post only on the
        happy path would make the hook useless for exactly the case it is for."""
        result = await _RaisingTool()()

        assert not result.success
        post = watcher.payload(POST_TOOL_CALL)
        assert post["success"] is False
        assert "blew up" in str(post["error"])

    async def test_with_no_hooks_the_tool_is_untouched(self) -> None:
        previous = HookRegistry._instance  # noqa: SLF001
        HookRegistry._instance = HookRegistry()  # noqa: SLF001
        try:
            result = await _ProbeTool()(argument="value")
        finally:
            HookRegistry._instance = previous  # noqa: SLF001

        assert result.success
        assert result.output == "done"


# ----------------------------------------------------------------- providers


class _ProbeProvider(ModelProvider):
    """Nothing injected, so ``_resilient_round`` is a pass-through — which is the
    configuration under test: the hook must fire on the plain path too."""

    @property
    def name(self) -> str:
        return "probe-provider"

    @property
    def protocol(self) -> str:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        async def _round() -> CompletionResult:
            return CompletionResult(
                content="hello", model=model, provider_name="probe-provider",
                input_tokens=1, output_tokens=1, duration_ms=1.0,
            )

        return await self._resilient_round(_round)  # noqa: SLF001

    def stream(self, messages: list[Message], model: str, **kwargs: object) -> Any:
        raise NotImplementedError


class TestTheProviderSeam:
    async def test_both_points_fire_around_a_remote_round(self, watcher: _Watcher) -> None:
        result = await _ProbeProvider().complete([], model="probe-model")

        assert result.content == "hello"
        assert watcher.points() == [PRE_LLM_CALL, POST_LLM_CALL]
        assert watcher.payload(PRE_LLM_CALL)["provider"] == "probe-provider"
        post = watcher.payload(POST_LLM_CALL)
        assert post["ok"] is True
        assert post["duration_ms"] is not None

    async def test_a_failed_round_is_reported_and_still_raises(
        self, watcher: _Watcher
    ) -> None:
        """The hook observes; it does not absorb. The caller's exception is
        unchanged — an observer that swallowed a provider fault would turn a
        visible outage into a silent one."""

        class _Broken(_ProbeProvider):
            async def complete(
                self, messages: list[Message], model: str, **kwargs: object
            ) -> CompletionResult:
                async def _round() -> CompletionResult:
                    raise RuntimeError("provider is down")

                return await self._resilient_round(_round)  # noqa: SLF001

        with pytest.raises(RuntimeError, match="provider is down"):
            await _Broken().complete([], model="probe-model")

        assert watcher.payload(POST_LLM_CALL)["ok"] is False


# ------------------------------------------------------------------ sessions


USER = "72055773"


@dataclass
class _Msg:
    channel: str = "telegram"
    is_direct: bool = True
    session_key: str = USER
    chat_id: int | None = 72055773


class _NoIdentityServices:
    identity_resolver = None


def _now(hour: int = 12, day: int = 26) -> datetime.datetime:
    return datetime.datetime(2026, 7, day, hour, 0, tzinfo=datetime.UTC).astimezone()


def _store(db: DbPool) -> SessionStore:
    """A store whose process start is PINNED, the way tests/sessions/conftest.py
    pins it for that directory.

    ESC-13 rolls a lane whose incarnation predates the running process. These
    tests drive fixed clocks in the past, and a real process always starts after
    them — so without the pin every second turn looks like a restart and the test
    measures the restart trigger instead of the boundary it was written for.
    """
    return SessionStore(db, process_started_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))


class TestTheSessionSeam:
    async def test_a_new_lane_starts_a_session(
        self, tmp_db: DbPool, watcher: _Watcher
    ) -> None:
        store = _store(tmp_db)

        _key, run, _ = await resolve_turn_session(
            _Msg(), owl_name="secretary", session_store=store,
            session_settings=SessionSettings(), services=_NoIdentityServices(),
            now=_now(20, day=25),
        )

        start = watcher.payload(ON_SESSION_START)
        assert start["conversation_id"] == run
        assert start["owl_name"] == "secretary"
        assert start["channel"] == "telegram"
        assert start["previous_conversation_id"] is None, "a first lane has no predecessor"
        assert ON_SESSION_END not in watcher.points(), "nothing ended — nothing existed"

    async def test_a_rollover_ends_one_session_and_starts_the_next(
        self, tmp_db: DbPool, watcher: _Watcher
    ) -> None:
        """The order matters and is asserted: END then START. A consumer summarising
        the conversation that just closed must not be handed the new one first."""
        store = _store(tmp_db)
        kw = dict(owl_name="secretary", session_store=store,
                  session_settings=SessionSettings(), services=_NoIdentityServices())

        _k1, run1, _ = await resolve_turn_session(_Msg(), now=_now(20, day=25), **kw)  # type: ignore[arg-type]
        watcher.events.clear()
        _k2, run2, _ = await resolve_turn_session(_Msg(), now=_now(9, day=26), **kw)  # type: ignore[arg-type]

        assert run2 != run1, "the fixture must actually cross a boundary"
        assert watcher.points() == [ON_SESSION_END, ON_SESSION_START]
        end = watcher.payload(ON_SESSION_END)
        assert end["conversation_id"] == run1
        assert end["reason"], "a boundary always has a reason"
        assert watcher.payload(ON_SESSION_START)["previous_conversation_id"] == run1

    async def test_a_process_restart_is_not_a_new_conversation(
        self, tmp_db: DbPool, watcher: _Watcher
    ) -> None:
        """ESC-13 mints a fresh conversation_id when the process that froze the prompt is
        gone, and deliberately does NOT tell the user a conversation ended. A hook
        is told exactly what the user is: a start with no matching end would be an
        observer's view of a boundary the platform does not believe happened."""
        store = SessionStore(
            tmp_db, process_started_at=datetime.datetime(2026, 7, 26, 11, tzinfo=datetime.UTC)
        )
        kw = dict(owl_name="secretary", session_store=store,
                  session_settings=SessionSettings(), services=_NoIdentityServices())

        _k1, run1, _ = await resolve_turn_session(_Msg(), now=_now(10, day=26), **kw)  # type: ignore[arg-type]
        watcher.events.clear()
        _k2, run2, _ = await resolve_turn_session(_Msg(), now=_now(12, day=26), **kw)  # type: ignore[arg-type]

        assert run2 != run1, "the fixture must actually exercise the restart trigger"
        assert watcher.events == []

    async def test_a_quiet_turn_starts_nothing(
        self, tmp_db: DbPool, watcher: _Watcher
    ) -> None:
        """A hook fires per CONVERSATION, not per message. on_session_start on every
        turn would be a rename of 'a message arrived'."""
        store = _store(tmp_db)
        kw = dict(owl_name="secretary", session_store=store,
                  session_settings=SessionSettings(), services=_NoIdentityServices())

        await resolve_turn_session(_Msg(), now=_now(12, day=26), **kw)  # type: ignore[arg-type]
        watcher.events.clear()
        await resolve_turn_session(_Msg(), now=_now(13, day=26), **kw)  # type: ignore[arg-type]

        assert watcher.events == []


class TestTheClockBoundaryTellsHooksToo:
    """THE WAY A CONVERSATION ACTUALLY ENDS IS BY GOING QUIET, and until 2026-08-20
    that ending reached no hook at all.

    Found at D16.1's validate stage, watching for an ``on_session_end`` that never
    came. There are TWO places the platform recognises a conversation boundary:

      resolve_for    — the TRAFFIC path: the user spoke again, and the gap since
                       their last message crossed the policy.
      sweep          — the CLOCK path: nobody spoke, and the sweeper finalises the
                       lane on schedule. Its own docstring says why it exists —
                       "without this, a rollover only happens when the user next
                       sends a message, so the 4 AM boundary would really mean
                       'whenever you next say something'".

    The hook was wired at the first only. Worse, the two interact: ``resolve_for``
    suppresses its dispatch when ``existing.expiry_finalized``, precisely so one
    boundary is not announced twice. That guard was written for the EVENT BUS
    consumer, which the sweeper does notify via ``_publish_rollover``. The hook
    dispatch was later added inside the guard and inherited a suppression meant for
    a publisher it does not share — so a lane the sweeper finalised could never
    reach a hook by either route.

    Net effect: a hook saw a conversation end only when the user happened to come
    back before the sweeper got there. That is an actuator wired on one path of two,
    the first failure shape in PROCESS.md, sitting inside the item built to avoid it.

    NO ``on_session_start`` HERE, and that asymmetry is deliberate: the sweeper
    finalises rather than mints, because "minting here would hand out an incarnation
    nobody is using and start a conversation the user never opened". The next
    inbound message mints it through the normal path, which dispatches START then.
    """

    async def test_a_lane_the_sweeper_expires_ends_for_hooks(
        self, tmp_db: DbPool, watcher: _Watcher
    ) -> None:
        store = _store(tmp_db)
        kw = dict(owl_name="secretary", session_store=store,
                  session_settings=SessionSettings(), services=_NoIdentityServices())
        _k, run1, _ = await resolve_turn_session(_Msg(), now=_now(20, day=25), **kw)  # type: ignore[arg-type]
        watcher.events.clear()

        finalized, _skipped = await store.sweep(now=_now(9, day=26))

        assert finalized == 1, "the fixture must actually expire the lane"
        assert watcher.points() == [ON_SESSION_END], (
            "the clock boundary reached no hook"
        )
        end = watcher.payload(ON_SESSION_END)
        assert end["conversation_id"] == run1
        assert end["reason"], "a boundary always has a reason"
        assert end["owl_name"] == "secretary"
        assert end["channel"] == "telegram"

    async def test_a_sweep_that_finalises_nothing_says_nothing(
        self, tmp_db: DbPool, watcher: _Watcher
    ) -> None:
        """The sweep runs every minute against every lane. A hook firing on an
        uneventful pass would be a rename of 'the sweeper ran'."""
        store = _store(tmp_db)
        await resolve_turn_session(
            _Msg(), owl_name="secretary", session_store=store,
            session_settings=SessionSettings(), services=_NoIdentityServices(),
            now=_now(12, day=26),
        )
        watcher.events.clear()

        finalized, _ = await store.sweep(now=_now(13, day=26))

        assert finalized == 0
        assert watcher.events == []

    async def test_one_boundary_is_announced_once(
        self, tmp_db: DbPool, watcher: _Watcher
    ) -> None:
        """The double-announce guard must still hold. After the sweeper ends a lane,
        the user's next message mints the new incarnation — and must START it
        without ENDING the same conversation a second time."""
        store = _store(tmp_db)
        kw = dict(owl_name="secretary", session_store=store,
                  session_settings=SessionSettings(), services=_NoIdentityServices())
        await resolve_turn_session(_Msg(), now=_now(20, day=25), **kw)  # type: ignore[arg-type]
        await store.sweep(now=_now(9, day=26))
        watcher.events.clear()

        await resolve_turn_session(_Msg(), now=_now(10, day=26), **kw)  # type: ignore[arg-type]

        assert ON_SESSION_END not in watcher.points(), "announced the same ending twice"

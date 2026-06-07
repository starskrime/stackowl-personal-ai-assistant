"""DELEGATION SELF-HEALING SMOKE — honest / recover / no-escalate / cycle.

Four end-to-end gateway journeys proving the delegation self-healing arc (plan
``2026-06-06-delegation-self-healing.md``, tasks DT1-DT9) with REAL wiring. A
real inbound Telegram update traverses the GENUINE path (TelegramChannelAdapter →
GatewayScanner → AsyncioBackend pipeline → ``execute._dispatch`` → ToolRegistry →
DelegateTaskTool → a REAL ``A2ADelegator.delegate`` → a CHILD ``AsyncioBackend.run``
sub-pipeline → the reply over the real ``A2AQueue`` → the parent surfaces it).

The ONLY mock is the AI provider (``_ScriptedProvider``, owl-aware — branches on
``TraceContext.get()["owl_name"]``). Everything else is production code: the
``OwlRegistry``, the real ``A2ADelegator`` on ``services.a2a_delegator``, the real
``A2AQueue``, the real ``ConcurrencyGovernor``, the real ``execute`` dispatch +
the real Epic-2 bounds enforcement seam (``execute._dispatch`` →
``compute_effective_bounds`` → ``check_effective_bounds``), the real
``surface_critical_failure`` safety-net, and a fully-migrated ``tmp_db``.

Journeys:

  (A) HONEST FAILURE — a delegated child fails (empty sub-run) → the parent's
      final delivered Telegram message is NON-EMPTY and HONEST (not a fabricated
      answer), via the model status OR the safety-net surfacing.

  (B) FALLBACK RECOVERY + ATTRIBUTION — caller is a NON-secretary specialist; it
      delegates to another specialist that fails twice (empty) → the recovery
      ladder falls back to the Secretary (same floor) which succeeds → the record
      status is ``recovered_via_secretary`` and the user gets the answer WITH the
      attributed lead-in.

  (C) LOAD-BEARING NO-ESCALATION — a NARROW specialist whose ``bounds`` EXCLUDE
      ``shell`` delegates a task whose child execution needs ``shell``. Both the
      target child AND the Secretary fallback run clamped to the narrow floor
      (``creation_ceiling = child_floor(narrow_specialist, ...)``). The REAL
      dispatch seam DENIES ``shell`` to BOTH (a real ``_RecordingTool`` named
      ``shell`` whose ``.runs`` counter proves it never executed) → an honest
      failure. The Secretary did NOT gain ``shell`` via the fallback. This proves
      a fallback cannot launder a forbidden tool.

  (D) CYCLE → HONEST, NO HANG — a turn whose resolved delegation target is
      already in the seeded ``delegation_chain`` → ``cycle`` status, an honest
      message, and a prompt return (the test completing under the harness is the
      no-hang proof).

Scaffolding (``_FakeBot``/``_FakeBotApp``, ``_turn``, the ``_live_io`` autouse
fixture, the env wiring) is REUSED from the sibling
``test_e8_s1_delegate_task_telegram_smoke.py``. The ``_RecordingTool`` (a real
Tool whose ``.runs`` counter proves execution vs denial) is REUSED in shape from
``tests/journeys/test_skill_injection_journey.py``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from stackowl.authz.bounds import BoundsSpec
from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner
from stackowl.infra.trace import TraceContext
from stackowl.messaging.a2a import A2AQueue
from stackowl.owls.a2a_delegation import A2ADelegator
from stackowl.owls.concurrency import ConcurrencyGovernor
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.persistence import TOOL_FAILED_MARKER
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 747474

# Canonical bounds-deny reason produced by the REAL dispatch seam
# (authz/bounds_guard.check_effective_bounds). Asserted in journey C.
_DENY_FRAGMENT = "not permitted by this owl's bounds"


# ---------------------------------------------------------------------------
# FAKED #1: Telegram bot HTTP transport (REUSED from e8_s1)
# ---------------------------------------------------------------------------


class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):  # noqa: ANN001
        self.messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    async def answer_callback_query(self, callback_id, text=None):  # noqa: ANN001
        pass


class _FakeBotApp:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot

    def add_handler(self, handler: object) -> None:
        pass


# ---------------------------------------------------------------------------
# REAL tool: a recording tool whose .runs counter proves execution vs denial.
# REUSED shape from tests/journeys/test_skill_injection_journey.py.
# ---------------------------------------------------------------------------


class _RecordingTool(Tool):
    def __init__(self, name: str, output: str, *, toolset_group: str | None = None) -> None:
        self._name = name
        self._output = output
        self._toolset_group = toolset_group
        self.runs = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Records execution of {self._name}."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self.description,
            parameters=self.parameters, action_severity="read",
            toolset_group=self._toolset_group,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.runs += 1
        return ToolResult(success=True, output=self._output, error=None, duration_ms=1.0)


# ---------------------------------------------------------------------------
# FAKED #2 (THE ONLY AI MOCK): an owl-aware scripted provider.
#
# Each journey supplies a ``script`` callable that, given the running owl_name
# and a ``tool_dispatcher``, returns ``(final_text, tool_calls)`` for that owl's
# (sub-)pipeline. This is the e8_s1 pattern generalized: the parent typically
# emits a delegate_task call and surfaces the record; a child typically returns
# a plain final answer (or runs a tool first, e.g. journey C's shell attempt).
# ---------------------------------------------------------------------------


class _ScriptedProvider:
    protocol = "anthropic"

    def __init__(self, script) -> None:  # noqa: ANN001
        self._script = script
        self.parent_results: list[str] = []
        self.owls_run: list[str] = []

    @property
    def name(self) -> str:
        # The router (triage step) reads provider.name for its decision log.
        return "scripted"

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None, **_kw):  # noqa: ANN001
        ctx = TraceContext.get()
        owl = str(ctx.get("owl_name") or "")
        self.owls_run.append(owl)
        return await self._script(self, owl, tool_dispatcher)

    async def complete(self, messages, model="", **k) -> CompletionResult:  # noqa: ANN001
        # Used by the router (triage) and the safety-net apology cascade. Returning
        # non-empty content keeps both real paths exercised (mock only the AI).
        return CompletionResult(
            content="ok", input_tokens=1, output_tokens=1, model="scripted",
            provider_name="scripted", duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover — not on this path
        if False:
            yield ""


class _FakeProviderRegistry:
    def __init__(self, p: _ScriptedProvider) -> None:
        self._p = p

    def get(self, name: str) -> _ScriptedProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _ScriptedProvider:
        return self._p

    def get_with_cascade(self, preferred_tier: str) -> _ScriptedProvider:
        return self._p


# ---------------------------------------------------------------------------
# Env wiring (modeled on the e8_s1 harness)
# ---------------------------------------------------------------------------


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: _ScriptedProvider
    owl_registry: OwlRegistry
    tool_registry: ToolRegistry
    governor: ConcurrencyGovernor


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _build(
    *, tmp_db: DbPool, provider: _ScriptedProvider, owl_registry: OwlRegistry,
    tool_registry: ToolRegistry, timeout_seconds: float = 2.0,
) -> _Env:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)  # type: ignore[assignment]
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    governor = ConcurrencyGovernor()
    a2a_queue = A2AQueue()
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=tool_registry,
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        owl_registry=owl_registry,
        a2a_queue=a2a_queue,
        delegation_governor=governor,
        db_pool=tmp_db,  # REAL, fully-migrated DbPool
    )
    # REAL A2ADelegator wired off the same queue/governor/services single source.
    # A short timeout keeps a genuine-timeout path deterministic without a 30s wait.
    services.a2a_delegator = A2ADelegator(
        a2a_queue=a2a_queue, services=services, timeout_seconds=timeout_seconds
    )
    return _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=owl_registry),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider, owl_registry=owl_registry, tool_registry=tool_registry,
        governor=governor,
    )


async def _turn(env: _Env, text: str) -> str:
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    msg = await env.adapter.receive()
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    _writer, reader = env.stream_registry.create(msg.session_id)
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
    )
    before = len(env.bot.messages)
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.session_id)
    return "\n".join(
        m["text"] for m in env.bot.messages[before:]
        if m["chat_id"] == USER_ID and m["reply_markup"] is None
    )


# ---------------------------------------------------------------------------
# Registry / tool-registry helpers
# ---------------------------------------------------------------------------


def _specialist(name: str, role: str, *, bounds: BoundsSpec | None = None) -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name, role=role, system_prompt=f"You are {name}.",
        model_tier="standard", bounds=bounds,
    )


def _tools(*extra: Tool) -> ToolRegistry:
    """A ToolRegistry with delegate_task (real) plus any extra tools."""
    reg = ToolRegistry.with_defaults()
    for t in extra:
        reg.register(t)
    return reg


# ===========================================================================
# JOURNEY A — child failure surfaces honestly (no silent swallow)
# ===========================================================================


@pytest.mark.asyncio
async def test_child_failure_surfaces_honestly(tmp_db: DbPool) -> None:
    """A delegated child fails (empty sub-run) → the parent surfaces an HONEST,
    non-empty FAILED message to the user, never a silent swallow nor a fabricated
    answer.

    New contract (D2): an unbounded child (bounds=None) is conservatively treated as
    write-capable by ``_can_side_effect`` → on failure the delegation halts with an
    honest-uncertain FAILED terminal (``success=False``, prose in ``tr.error``,
    marker-prefixed by the dispatch seam). The parent reads the prose directly
    (not a JSON envelope).
    """

    async def script(prov: _ScriptedProvider, owl: str, dispatch):  # noqa: ANN001
        if owl == "scout":
            # CHILD: produce NO answer → governor decides status "empty".
            return ("", [])
        # PARENT (secretary): delegate to scout. Under the new contract, the
        # observation for a FAILED delegation is honest FAILED prose (marker-prefixed),
        # NOT a JSON envelope — the parent relays it verbatim so no text is invented.
        out = await dispatch("delegate_task", {"goal": "research X", "to_owl": "scout"})
        prov.parent_results.append(out)
        prose = out.replace(TOOL_FAILED_MARKER, "")
        return (prose or out, [
            {"name": "delegate_task", "args": {"to_owl": "scout"}, "result": out},
        ])

    reg = OwlRegistry.with_default_secretary()
    reg.register(_specialist("scout", "research-scout"))
    env = _build(tmp_db=tmp_db, provider=_ScriptedProvider(script),
                 owl_registry=reg, tool_registry=_tools())

    delivered = await _turn(env, "research X for me")

    # The failed delegation surfaces as honest FAILED prose (not a JSON envelope).
    assert env.provider.parent_results, "parent never reached delegate_task"
    observation = env.provider.parent_results[0]
    # The dispatch seam prepends TOOL_FAILED_MARKER when success=False.
    assert TOOL_FAILED_MARKER in observation, (
        f"expected FAILED marker in observation — delegation did not return honest failure:\n{observation!r}"
    )
    assert "FAILED" in observation, (
        f"honest FAILED prose missing from observation:\n{observation!r}"
    )

    # The child sub-pipeline genuinely ran (real delegation round-trip).
    assert "scout" in env.provider.owls_run, env.provider.owls_run

    # The USER sees a NON-EMPTY, non-silent outcome over Telegram.
    assert delivered.strip(), f"silent swallow: user got an empty message; got {delivered!r}"
    # Governor self-healed: the child slot was released.
    assert env.governor.in_flight == 0


# ===========================================================================
# JOURNEY B — fallback to secretary recovers, with attribution
# ===========================================================================


@pytest.mark.asyncio
async def test_fallback_to_secretary_recovers_with_attribution(tmp_db: DbPool) -> None:
    """Caller is a NON-secretary specialist (analyst). It delegates to scout which
    fails twice (empty) → the recovery ladder falls back to the Secretary, which
    answers → record status ``recovered_via_secretary`` and the user gets the
    secretary's answer WITH the attributed lead-in.

    New contract (D2): to exercise the recovery/fallback path the child MUST be
    READ-ONLY (``bounds`` restricted to read-severity tools only). An unbounded child
    is conservatively write-capable → halts with honest FAILED terminal (no fallback).
    Scout is given explicit read-only bounds so ``_can_side_effect`` returns False and
    the self-healing ladder (same-owl retry + secretary fallback) runs as intended.
    Intent preserved: a safe (read-only) delegation self-heals via the secretary.
    """

    secretary_answer = "Secretary's recovered answer: 42."

    async def script(prov: _ScriptedProvider, owl: str, dispatch):  # noqa: ANN001
        if owl == "scout":
            return ("", [])  # read-only specialist fails → empty (retriable)
        if owl == "secretary":
            return (secretary_answer, [])  # fallback succeeds
        # PARENT (owl=analyst, a non-secretary specialist): delegate to scout.
        # Recovery produces a success=True JSON envelope (recovered_via_secretary).
        out = await dispatch("delegate_task", {"goal": "do the thing", "to_owl": "scout"})
        prov.parent_results.append(out)
        record = json.loads(out).get("record", {})
        return (str(record.get("result", out)), [
            {"name": "delegate_task", "args": {"to_owl": "scout"}, "result": out},
        ])

    reg = OwlRegistry.with_default_secretary()
    # Scout is READ-ONLY: bounds restricted to web_search (action_severity="read") so
    # _can_side_effect("scout") returns False → retry/fallback ladder is allowed.
    reg.register(_specialist("scout", "research-scout",
                             bounds=BoundsSpec(tools=frozenset({"web_search"}))))
    reg.register(_specialist("analyst", "data-analyst"))
    env = _build(tmp_db=tmp_db, provider=_ScriptedProvider(script),
                 owl_registry=reg, tool_registry=_tools())

    # Route the turn so owl_name == analyst (the non-secretary caller).
    delivered = await _turn(env, "@analyst do the thing")

    assert env.provider.parent_results, "analyst never reached delegate_task"
    record = json.loads(env.provider.parent_results[0])["record"]
    assert record["status"] == "recovered_via_secretary", record
    assert secretary_answer.rstrip(".") in str(record["result"]), record
    # Attributed lead-in flags the substitution (scout failed → secretary handled it).
    assert "secretary" in str(record["result"]).lower(), record
    assert "scout" in str(record["result"]).lower(), record

    # Both the failing specialist and the recovering secretary genuinely ran.
    assert "scout" in env.provider.owls_run and "secretary" in env.provider.owls_run, env.provider.owls_run

    # The USER receives the secretary's recovered answer + the substitution lead-in.
    assert "Secretary's recovered answer: 42" in delivered, delivered
    assert "secretary" in delivered.lower(), delivered
    assert env.governor.in_flight == 0


# ===========================================================================
# JOURNEY C — LOAD-BEARING: narrow fallback cannot escalate shell
# ===========================================================================


@pytest.mark.asyncio
async def test_narrow_fallback_cannot_escalate_shell(tmp_db: DbPool) -> None:
    """A NARROW specialist (bounds exclude ``shell``) delegates a task whose child
    execution needs ``shell``. Both the target child AND the Secretary fallback run
    clamped to the narrow floor (``creation_ceiling``), so the REAL dispatch seam
    DENIES ``shell`` to BOTH. The recording tool's ``.runs`` stays 0 (shell never
    executed) and the outcome is an HONEST failure — the Secretary did NOT gain
    ``shell`` via the fallback. Proves a fallback can't launder a forbidden tool.
    """
    # A minimal registry (NOT with_defaults — its real consequential ShellTool may
    # not be shadowed): the REAL DelegateTaskTool plus a recording 'shell' whose
    # .runs counter proves whether the tool's execute actually ran.
    from stackowl.tools.agents.delegate_task import DelegateTaskTool

    shell = _RecordingTool("shell", "SHOULD-NEVER-APPEAR", toolset_group="sysadmin")
    tool_registry = ToolRegistry()
    tool_registry.register(DelegateTaskTool())
    tool_registry.register(shell)

    # The narrow specialist may delegate but NOT run shell.
    narrow_bounds = BoundsSpec(tools=frozenset({"delegate_task"}))
    assert "shell" not in narrow_bounds.tools  # premise guard
    reg = OwlRegistry.with_default_secretary()
    reg.register(_specialist("narrow", "narrow-specialist", bounds=narrow_bounds))
    # Worker is given explicit READ-ONLY bounds (restricted to the recording 'shell'
    # tool which has action_severity="read" in this test's _RecordingTool). This makes
    # _can_side_effect("worker") return False so the delegation ladder allows the
    # secretary fallback to run — both child AND secretary are then clamped to the
    # narrow floor and denied the forbidden shell. This is the new D2 contract:
    # only a read-only child enables the fallback/retry ladder.
    worker_bounds = BoundsSpec(tools=frozenset({"shell"}))
    reg.register(_specialist("worker", "worker", bounds=worker_bounds))

    async def script(prov: _ScriptedProvider, owl: str, dispatch):  # noqa: ANN001
        if owl in {"worker", "secretary"}:
            # BOTH the target child and the secretary fallback try to run shell.
            # The REAL dispatch seam denies it (effective bounds clamp to the
            # narrow floor). With shell denied the child has no way to do the task,
            # so it returns NO answer AND records no tool_call → the child sub-run
            # is honestly "empty" (retriable for the target; terminal honest-fail
            # after the secretary fallback also can't run shell). We deliberately
            # omit the tool_call record from the return so the consolidate step
            # does not surface the deny text as a fabricated "answer".
            out = await dispatch("shell", {})
            prov.parent_results.append(f"{owl}:{out}")
            return ("", [])
        # PARENT (owl=narrow): delegate a shell-needing task to worker. Under the new
        # contract, when the entire ladder exhausts without recovery the outcome is an
        # honest FAILED prose (success=False, marker-prefixed) — NOT a JSON envelope.
        out = await dispatch("delegate_task", {"goal": "run a shell command", "to_owl": "worker"})
        prov.parent_results.append(f"narrow:{out}")
        prose = out.replace(TOOL_FAILED_MARKER, "")
        return (prose or out, [
            {"name": "delegate_task", "args": {"to_owl": "worker"}, "result": out},
        ])

    env = _build(tmp_db=tmp_db, provider=_ScriptedProvider(script),
                 owl_registry=reg, tool_registry=tool_registry)

    delivered = await _turn(env, "@narrow run a shell command for me")

    # --- shell NEVER executed (the load-bearing security proof) ----------------
    assert shell.runs == 0, (
        "AUTHORIZATION BREACH: shell executed even though neither the narrow "
        "specialist nor the secretary-fallback is permitted it under the narrow floor"
    )

    # --- BOTH the target child AND the secretary fallback were DENIED shell -----
    # at the REAL dispatch seam (canonical bounds-deny reason).
    shell_denies = [r for r in env.provider.parent_results if _DENY_FRAGMENT in r]
    assert any(r.startswith("worker:") for r in shell_denies), env.provider.parent_results
    assert any(r.startswith("secretary:") for r in shell_denies), (
        "the SECRETARY fallback was NOT denied shell — a fallback escalated the "
        f"forbidden tool. results={env.provider.parent_results}"
    )

    # --- the secretary genuinely ran as the fallback (clamped to the floor) -----
    assert "secretary" in env.provider.owls_run, env.provider.owls_run

    # --- the parent sees an HONEST FAILED terminal (prose, not JSON) ------------
    # After worker+secretary both fail, honest_irrelevant_result returns success=False
    # prose. The parent_results entry for "narrow:" contains the raw dispatch output
    # (marker-prefixed FAILED prose).
    parent_out = next(r for r in env.provider.parent_results if r.startswith("narrow:"))
    raw_delegation = parent_out.split("narrow:", 1)[1]
    assert TOOL_FAILED_MARKER in raw_delegation, (
        f"expected FAILED marker in parent delegation observation:\n{raw_delegation!r}"
    )
    assert "FAILED" in raw_delegation, (
        f"honest FAILED prose missing from parent delegation observation:\n{raw_delegation!r}"
    )

    # --- the user gets a NON-EMPTY honest message (no silent swallow) -----------
    assert delivered.strip(), f"silent swallow on a denied-tool failure; got {delivered!r}"
    assert env.governor.in_flight == 0


# ===========================================================================
# JOURNEY D — cycle surfaces honestly, no hang
# ===========================================================================


@pytest.mark.asyncio
async def test_cycle_surfaces_without_hang(tmp_db: DbPool) -> None:
    """A turn whose resolved delegation target is ALREADY in the seeded
    ``delegation_chain`` → the tool refuses with ``cycle`` BEFORE spawning any
    child, surfaces an honest message, and returns promptly. The test completing
    under the harness (no real delegate spawn, no await) is the no-hang proof."""

    async def script(prov: _ScriptedProvider, owl: str, dispatch):  # noqa: ANN001
        # PARENT (secretary): try to delegate to scout — which is already in the
        # seeded chain → cycle refusal (delegate() is NEVER reached).
        out = await dispatch("delegate_task", {"goal": "loop me", "to_owl": "scout"})
        prov.parent_results.append(out)
        record = json.loads(out).get("record", {})
        return (str(record.get("detail") or out), [
            {"name": "delegate_task", "args": {"to_owl": "scout"}, "result": out},
        ])

    reg = OwlRegistry.with_default_secretary()
    reg.register(_specialist("scout", "research-scout"))
    env = _build(tmp_db=tmp_db, provider=_ScriptedProvider(script),
                 owl_registry=reg, tool_registry=_tools())

    # Seed the delegation_chain so the resolved target (scout) is already present.
    # The pipeline threads state.delegation_chain → TraceContext → the cycle check.
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text="research X"),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    msg = await env.adapter.receive()
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    _writer, reader = env.stream_registry.create(msg.session_id)
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
        delegation_chain=("scout",),  # SEED: scout already in the chain → cycle
    )
    before = len(env.bot.messages)
    # asyncio.wait_for is the explicit no-hang guard: a cycle that looped/blocked
    # would trip the timeout and fail the test loudly rather than hang the suite.
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await asyncio.wait_for(run_task, timeout=10.0)
    await asyncio.wait_for(out_task, timeout=10.0)
    env.stream_registry.remove(msg.session_id)
    delivered = "\n".join(
        m["text"] for m in env.bot.messages[before:]
        if m["chat_id"] == USER_ID and m["reply_markup"] is None
    )

    # The delegate record is a cycle refusal — delegate() was NEVER reached, so no
    # child sub-pipeline ran (only the parent secretary turn).
    assert env.provider.parent_results, "parent never reached delegate_task"
    record = json.loads(env.provider.parent_results[0])["record"]
    assert record["status"] == "cycle", record
    assert env.provider.owls_run == ["secretary"], (
        f"a child spawned despite the cycle refusal: {env.provider.owls_run}"
    )

    # The USER gets a NON-EMPTY honest message (no silent swallow, no hang).
    assert delivered.strip(), f"silent swallow on cycle; got {delivered!r}"
    assert env.governor.in_flight == 0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])

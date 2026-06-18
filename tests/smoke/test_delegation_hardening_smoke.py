"""DELEGATION HARDENING SMOKE — merge-gate, off-topic self-heal, fail-open.

Six end-to-end gateway journeys proving the D2 (side-effect-aware retry/dedup) +
D3 (two-stage relevance gate) merge-gate invariants with REAL wiring. A real
inbound Telegram update traverses the GENUINE path (TelegramChannelAdapter →
GatewayScanner → AsyncioBackend pipeline → ``execute._dispatch`` → ToolRegistry →
DelegateTaskTool → a REAL ``A2ADelegator.delegate`` → a CHILD ``AsyncioBackend.run``
sub-pipeline → the reply over the real ``A2AQueue`` → the parent surfaces it).

The ONLY mocks are (1) the AI provider (``_ScriptedProvider``, owl-aware — branches
on ``TraceContext.get()["owl_name"]``) and (2) the LLM relevance judge, which is
monkeypatched at ``stackowl.tools.agents.delegate_task.judge_relevance`` per
journey (a deterministic async stub — simpler + more reliable than crafting the
scripted provider to detect a judge call). Everything else is production code: the
``OwlRegistry``, the real ``A2ADelegator``, the real ``A2AQueue``, the real
``ConcurrencyGovernor``, the real ``execute`` dispatch + the real Epic-2 bounds
enforcement seam, the real ``surface_critical_failure`` safety-net, and a
fully-migrated ``tmp_db``.

The UNIFIED MERGE GATE under test: only a READ-ONLY child is ever re-delegated; a
WRITE-CAPABLE child failure/off-topic-ok → an HONEST TERMINAL (no double
execution, no false success). Tool severities are now correct — a recording
``write_file``/``shell`` tool carries ``action_severity="write"`` (→ write-capable
owl) and a recording ``read_file``/``web_search`` carries ``action_severity="read"``
(→ read-only owl).

Journeys:

  (J1) MERGE-GATE — a WRITE-CAPABLE child RUNS its side-effecting tool (counter++),
       then the delegation FAILS retriably (empty) → the side-effect counter == 1
       (NOT 2), NO second delegation, the parent's observation is the honest
       FAILED-uncertain message. Never a false success, never a double action.
       THIS journey gates the merge.

  (J2) READ-ONLY child fails retriably → DOES retry/fallback (the safe class
       self-heals): ≥2 delegate attempts, then recovery via the secretary.

  (J3) READ-ONLY child returns an ok the judge demotes (off-topic) → SKIP same-owl
       retry → fallback to secretary → secretary relevant → recovered_via_secretary.
       Variant J3b: all off-topic → honest-irrelevant FAILED (not a false ok).

  (J3w) WRITE-CAPABLE child returns an off-topic ok (ran its tool, counter==1) →
        judge demotes → NO re-delegation → honest-off-topic-write FAILED; counter 1.

  (J4) the judge errors EVERY call → fail-open: the ok is DELIVERED, a WARN is
       logged, and ``judge_error_count()`` increased. Proves the feature isn't
       silently off.

  (J5) parent makes TWO delegations with DIFFERENT goals to the same owl in one
       turn → two REAL delegations (no false dedup); both ran.

Scaffolding (``_FakeBot``/``_FakeBotApp``, ``_RecordingTool``, ``_ScriptedProvider``,
``_FakeProviderRegistry``, ``_build``, ``_turn``, the ``_live_io`` autouse fixture)
is REUSED in shape from the sibling
``test_delegation_self_healing_smoke.py`` (S3 scaffold). The ``_RecordingTool``
gains an ``action_severity`` parameter so a child can be made write-capable or
read-only via REAL tool severities.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import stackowl.tools.agents.delegate_task as dt
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
from stackowl.pipeline.persistence import judge_error_count
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 747474


# ---------------------------------------------------------------------------
# FAKED #1: Telegram bot HTTP transport (REUSED from the S3 scaffold)
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
# REUSED shape from the S3 scaffold; EXTENDED with action_severity so a child can
# be made write-capable ("write") or read-only ("read") via REAL tool severities.
# ---------------------------------------------------------------------------


class _RecordingTool(Tool):
    def __init__(
        self,
        name: str,
        output: str,
        *,
        action_severity: str = "read",
        toolset_group: str | None = None,
    ) -> None:
        self._name = name
        self._output = output
        self._severity = action_severity
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
            parameters=self.parameters, action_severity=self._severity,
            toolset_group=self._toolset_group,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.runs += 1
        return ToolResult(success=True, output=self._output, error=None, duration_ms=1.0)


# ---------------------------------------------------------------------------
# FAKED #2 (THE ONLY AI MOCK): an owl-aware scripted provider.
# ---------------------------------------------------------------------------


class _ScriptedProvider:
    protocol = "anthropic"

    def __init__(self, script) -> None:  # noqa: ANN001
        self._script = script
        self.parent_results: list[str] = []
        self.owls_run: list[str] = []

    @property
    def name(self) -> str:
        return "scripted"

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None, **_kw):  # noqa: ANN001
        ctx = TraceContext.get()
        owl = str(ctx.get("owl_name") or "")
        self.owls_run.append(owl)
        return await self._script(self, owl, tool_dispatcher, user_text)

    async def complete(self, messages, model="", **k) -> CompletionResult:  # noqa: ANN001
        # Used by the router (triage) and the safety-net apology cascade. The
        # relevance judge is monkeypatched (NOT routed here), so this only ever
        # serves the agent-side cascades — non-empty content keeps both real.
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
        # D3 resolves the FAST provider once per ladder; returning a live provider
        # (not None) means the relevance gate's LLM stage genuinely fires — which is
        # exactly what the per-journey monkeypatched judge then governs.
        return self._p


# ---------------------------------------------------------------------------
# Env wiring (REUSED from the S3 scaffold)
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
    _writer, reader = env.stream_registry.create(msg.trace_id)
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
    )
    before = len(env.bot.messages)
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await asyncio.wait_for(run_task, timeout=15.0)
    await asyncio.wait_for(out_task, timeout=15.0)
    env.stream_registry.remove(msg.trace_id)
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


# Deterministic judge stubs (monkeypatch dt.judge_relevance per journey).
async def _judge_relevant(provider, ask, content):  # noqa: ANN001
    return (True, "on-topic")


async def _judge_offtopic(provider, ask, content):  # noqa: ANN001
    return (False, "off topic: did not address the request")


# The dispatch seam prepends TOOL_FAILED_MARKER to a success=False tool's rendered
# string. Strip it to recover the exact text the model reads.
def _strip_marker(out: str) -> str:
    from stackowl.pipeline.persistence import TOOL_FAILED_MARKER

    return out.replace(TOOL_FAILED_MARKER, "")


# A SUCCESSFUL delegate_task renders its JSON envelope as the tool observation; a
# FAILED one renders the honest PROSE (tr.error), not JSON. ``_record`` parses the
# success-path JSON envelope (used by the recovery/ok journeys).
def _record(out: str) -> dict:
    return json.loads(_strip_marker(out))["record"]


# Telegram MarkdownV2 escapes punctuation (', ., -) with backslashes on the way
# out. Strip backslashes so a substring assertion on user-visible text is robust.
def _unescape(s: str) -> str:
    return s.replace("\\", "")


# ===========================================================================
# JOURNEY J1 — MERGE-GATE: write-capable child acts once, fails, no re-delegation
# ===========================================================================


@pytest.mark.asyncio
async def test_j1_write_capable_failure_no_double_action(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A WRITE-CAPABLE child RUNS its side-effecting tool (counter++), then the
    delegation FAILS retriably (empty). The merge gate must NOT re-delegate: the
    side-effect counter stays == 1 (NOT 2), no second delegation runs, and the
    parent's tool observation is the honest-uncertain FAILED message. Never a false
    success, never a double action. THIS journey gates the merge.
    """
    monkeypatch.setattr(dt, "judge_relevance", _judge_relevant)

    writer = _RecordingTool("write_file", "wrote", action_severity="write")
    tool_registry = _tools_with(writer)

    async def script(prov: _ScriptedProvider, owl: str, dispatch, user_text):  # noqa: ANN001
        if owl == "writer_owl":
            # CHILD (write-capable): RUN the side-effecting write tool, THEN fail
            # retriably (return empty so the governor decides status "empty").
            await dispatch("write_file", {})
            return ("", [])
        # PARENT (secretary): delegate to the write-capable child, surface the
        # tool observation verbatim. A FAILED delegation surfaces the honest prose
        # (marker-prefixed by the dispatch seam), NOT a JSON envelope — so the model
        # relays exactly what it saw rather than inventing a success.
        out = await dispatch("delegate_task", {"goal": "write the file", "to_owl": "writer_owl"})
        prov.parent_results.append(out)
        return (_strip_marker(out), [
            {"name": "delegate_task", "args": {"to_owl": "writer_owl"}, "result": out},
        ])

    reg = OwlRegistry.with_default_secretary()
    reg.register(_specialist("writer_owl", "file-writer",
                             bounds=BoundsSpec(tools=frozenset({"write_file"}))))
    env = _build(tmp_db=tmp_db, provider=_ScriptedProvider(script),
                 owl_registry=reg, tool_registry=tool_registry)

    delivered = await _turn(env, "write the file for me")

    # --- THE MERGE GATE: the side effect happened EXACTLY ONCE -----------------
    assert writer.runs == 1, (
        f"DOUBLE-EXECUTION: the write tool ran {writer.runs} times — a write-capable "
        "child failure was re-delegated, duplicating a consequential action"
    )

    # --- NO second delegation: the child sub-pipeline ran ONCE -----------------
    assert env.provider.owls_run.count("writer_owl") == 1, (
        f"a write-capable child was re-delegated: owls_run={env.provider.owls_run}"
    )

    # --- the parent observation is the HONEST-UNCERTAIN FAILED message, NOT ok --
    # A failed delegation surfaces honest prose (not a JSON success envelope).
    assert env.provider.parent_results, "parent never reached delegate_task"
    observed = _strip_marker(env.provider.parent_results[0])
    assert "FAILED" in observed, observed
    assert "retr" in observed.lower(), observed  # "retry"/"retried" — honest, not a false success
    assert "may have" in observed.lower(), observed  # uncertain-about-side-effect framing

    # --- the USER sees a NON-EMPTY honest message (no false success) -----------
    assert delivered.strip(), f"silent swallow on write-capable failure; got {delivered!r}"
    assert env.governor.in_flight == 0


# ===========================================================================
# JOURNEY J2 — read-only failure self-heals (retry + fallback)
# ===========================================================================


@pytest.mark.asyncio
async def test_j2_read_only_failure_self_heals(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A READ-ONLY child fails retriably (empty) → the SAFE class self-heals: a
    same-owl retry, then a fallback to the secretary which recovers. Re-delegation
    happened (≥2 delegate attempts) and the outcome is a recovery (not a swallow).
    """
    monkeypatch.setattr(dt, "judge_relevance", _judge_relevant)

    reader = _RecordingTool("read_file", "read", action_severity="read")
    tool_registry = _tools_with(reader)
    secretary_answer = "Secretary recovered the read: contents here."

    async def script(prov: _ScriptedProvider, owl: str, dispatch, user_text):  # noqa: ANN001
        if owl == "reader_owl":
            return ("", [])  # read-only child fails retriably → empty
        if owl == "secretary":
            return (secretary_answer, [])  # fallback recovers
        # PARENT (analyst, a non-secretary specialist): delegate to the read-only child.
        out = await dispatch("delegate_task", {"goal": "read the file", "to_owl": "reader_owl"})
        prov.parent_results.append(out)
        record = _record(out)
        return (str(record.get("result") or out), [
            {"name": "delegate_task", "args": {"to_owl": "reader_owl"}, "result": out},
        ])

    reg = OwlRegistry.with_default_secretary()
    reg.register(_specialist("reader_owl", "file-reader",
                             bounds=BoundsSpec(tools=frozenset({"read_file"}))))
    reg.register(_specialist("analyst", "data-analyst"))
    env = _build(tmp_db=tmp_db, provider=_ScriptedProvider(script),
                 owl_registry=reg, tool_registry=tool_registry)

    delivered = await _turn(env, "@analyst read the file")

    # --- the SAFE class self-healed: ≥2 delegate attempts -----------------------
    # reader_owl ran twice (initial + same-owl retry); secretary ran as fallback.
    assert env.provider.owls_run.count("reader_owl") == 2, (
        f"read-only child was NOT retried: owls_run={env.provider.owls_run}"
    )
    assert "secretary" in env.provider.owls_run, env.provider.owls_run
    total_delegate_attempts = (
        env.provider.owls_run.count("reader_owl") + env.provider.owls_run.count("secretary")
    )
    assert total_delegate_attempts >= 2, total_delegate_attempts

    # --- recovery (not a swallow): recovered_via_secretary ----------------------
    assert env.provider.parent_results, "analyst never reached delegate_task"
    record = _record(env.provider.parent_results[0])
    assert record["status"] == "recovered_via_secretary", record
    assert "Secretary recovered the read" in str(record["result"]), record

    assert "Secretary recovered the read" in _unescape(delivered), delivered
    assert env.governor.in_flight == 0


# ===========================================================================
# JOURNEY J3 — read-only off-topic ok → fallback to secretary recovers
# ===========================================================================


@pytest.mark.asyncio
async def test_j3_read_only_offtopic_fallback_recovers(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A READ-ONLY child returns an ok the judge demotes (off-topic) → SKIP the
    same-owl retry (off_topic is not a transport failure) → fallback to the
    secretary, whose ok the judge passes → recovered_via_secretary (legible).
    """
    secretary_answer = "Secretary's on-topic answer: the file says 42."

    async def _selective_judge(provider, ask, content):  # noqa: ANN001
        # The read-only child's answer is off-topic; the secretary's is on-topic.
        return ("on-topic answer" in content, "verdict")

    monkeypatch.setattr(dt, "judge_relevance", _selective_judge)

    reader = _RecordingTool("read_file", "read", action_severity="read")
    tool_registry = _tools_with(reader)

    async def script(prov: _ScriptedProvider, owl: str, dispatch, user_text):  # noqa: ANN001
        if owl == "reader_owl":
            return ("x" * 60, [])  # substantive but off-topic (passes structural filter)
        if owl == "secretary":
            return (secretary_answer, [])  # on-topic → judge passes
        out = await dispatch("delegate_task", {"goal": "read the file", "to_owl": "reader_owl"})
        prov.parent_results.append(out)
        record = _record(out)
        return (str(record.get("result") or out), [
            {"name": "delegate_task", "args": {"to_owl": "reader_owl"}, "result": out},
        ])

    reg = OwlRegistry.with_default_secretary()
    reg.register(_specialist("reader_owl", "file-reader",
                             bounds=BoundsSpec(tools=frozenset({"read_file"}))))
    reg.register(_specialist("analyst", "data-analyst"))
    env = _build(tmp_db=tmp_db, provider=_ScriptedProvider(script),
                 owl_registry=reg, tool_registry=tool_registry)

    delivered = await _turn(env, "@analyst read the file")

    # off_topic SKIPS the same-owl retry → reader_owl ran ONCE then secretary.
    assert env.provider.owls_run.count("reader_owl") == 1, (
        f"off_topic should skip same-owl retry: owls_run={env.provider.owls_run}"
    )
    assert "secretary" in env.provider.owls_run, env.provider.owls_run

    record = _record(env.provider.parent_results[0])
    assert record["status"] == "recovered_via_secretary", record
    assert "on-topic answer" in str(record["result"]), record
    assert "Secretary's on-topic answer" in _unescape(delivered), delivered
    assert env.governor.in_flight == 0


@pytest.mark.asyncio
async def test_j3b_read_only_all_offtopic_honest_irrelevant(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VARIANT: a READ-ONLY child AND the secretary fallback both go off-topic →
    honest-irrelevant FAILED terminal, never a false ok.
    """
    monkeypatch.setattr(dt, "judge_relevance", _judge_offtopic)

    reader = _RecordingTool("read_file", "read", action_severity="read")
    tool_registry = _tools_with(reader)

    async def script(prov: _ScriptedProvider, owl: str, dispatch, user_text):  # noqa: ANN001
        if owl in {"reader_owl", "secretary"}:
            return ("y" * 60, [])  # substantive but always judged off-topic
        out = await dispatch("delegate_task", {"goal": "read the file", "to_owl": "reader_owl"})
        prov.parent_results.append(out)
        return (_strip_marker(out), [
            {"name": "delegate_task", "args": {"to_owl": "reader_owl"}, "result": out},
        ])

    reg = OwlRegistry.with_default_secretary()
    reg.register(_specialist("reader_owl", "file-reader",
                             bounds=BoundsSpec(tools=frozenset({"read_file"}))))
    reg.register(_specialist("analyst", "data-analyst"))
    env = _build(tmp_db=tmp_db, provider=_ScriptedProvider(script),
                 owl_registry=reg, tool_registry=tool_registry)

    delivered = await _turn(env, "@analyst read the file")

    # Both genuinely ran (child off-topic → fallback secretary off-topic).
    assert "reader_owl" in env.provider.owls_run and "secretary" in env.provider.owls_run, (
        env.provider.owls_run
    )
    # The terminal is the honest-irrelevant FAILED prose (NOT a false ok envelope).
    observed = _strip_marker(env.provider.parent_results[0])
    assert "FAILED" in observed, observed
    assert "did not address" in observed.lower(), observed
    assert delivered.strip(), f"silent swallow on all-off-topic; got {delivered!r}"
    assert env.governor.in_flight == 0


# ===========================================================================
# JOURNEY J3w — write-capable off-topic ok → NO re-delegation, honest FAILED
# ===========================================================================


@pytest.mark.asyncio
async def test_j3w_write_capable_offtopic_no_redelegation(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A WRITE-CAPABLE child returns an off-topic ok (it RAN its tool, counter==1) →
    the judge demotes → NO re-delegation (it may have acted) → honest-off-topic-write
    FAILED; the side-effect counter stays 1.
    """
    monkeypatch.setattr(dt, "judge_relevance", _judge_offtopic)

    writer = _RecordingTool("write_file", "wrote", action_severity="write")
    tool_registry = _tools_with(writer)

    async def script(prov: _ScriptedProvider, owl: str, dispatch, user_text):  # noqa: ANN001
        if owl == "writer_owl":
            # write-capable child RUNS its tool then returns a substantive (but
            # off-topic) answer → status "ok" → judge demotes to off_topic.
            await dispatch("write_file", {})
            return ("z" * 60, [])
        out = await dispatch("delegate_task", {"goal": "write the file", "to_owl": "writer_owl"})
        prov.parent_results.append(out)
        return (_strip_marker(out), [
            {"name": "delegate_task", "args": {"to_owl": "writer_owl"}, "result": out},
        ])

    # Caller is a non-secretary specialist (writer_boss) so that a secretary
    # FALLBACK would be eligible IF the gate wrongly re-delegated — making the
    # "no re-delegation" proof load-bearing (the secretary must NOT appear).
    reg = OwlRegistry.with_default_secretary()
    reg.register(_specialist("writer_owl", "file-writer",
                             bounds=BoundsSpec(tools=frozenset({"write_file"}))))
    reg.register(_specialist("writer_boss", "writer-boss"))
    env = _build(tmp_db=tmp_db, provider=_ScriptedProvider(script),
                 owl_registry=reg, tool_registry=tool_registry)

    delivered = await _turn(env, "@writer_boss write the file for me")

    # --- side effect happened exactly once; child NOT re-delegated --------------
    assert writer.runs == 1, f"write tool ran {writer.runs} times — off-topic write re-delegated"
    assert env.provider.owls_run.count("writer_owl") == 1, env.provider.owls_run
    # The caller is writer_boss (NOT secretary); a write-capable off-topic must NOT
    # fall back, so the secretary must never have been delegated to.
    assert "secretary" not in env.provider.owls_run, (
        f"write-capable off-topic was wrongly re-delegated to the secretary: {env.provider.owls_run}"
    )

    # The terminal is the honest-off-topic-write FAILED prose (NOT a false ok, and
    # explicitly flagging that the child was NOT re-delegated because it may have acted).
    observed = _strip_marker(env.provider.parent_results[0])
    assert "FAILED" in observed, observed
    assert "did not address" in observed.lower(), observed
    assert "not re-delegated" in observed.lower(), observed
    assert delivered.strip(), f"silent swallow on write off-topic; got {delivered!r}"
    assert env.governor.in_flight == 0


# ===========================================================================
# JOURNEY J4 — judge errors every call → fail-open (deliver + WARN + counter++)
# ===========================================================================


@pytest.mark.asyncio
async def test_j4_judge_error_fails_open(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The relevance judge ERRORS on every call → the gate fails OPEN: the child's
    ok is DELIVERED (not blocked), and ``judge_error_count()`` increases (the
    feature is provably NOT silently off). We monkeypatch the LOW-LEVEL provider
    call inside the REAL ``judge_relevance`` so the real fail-open path runs and
    bumps the real counter.
    """
    # Drive the REAL judge_relevance (NOT a stub) so its fail-open counter fires.
    # Force its provider.complete to raise → judge_relevance returns (True, "judge-error")
    # and increments _JUDGE_ERRORS. We patch the scripted provider's complete to raise
    # ONLY when called by the judge (it is the judge's provider via get_with_cascade).
    async def _boom_complete(messages, model="", **k):  # noqa: ANN001
        raise RuntimeError("judge provider down")

    reader = _RecordingTool("read_file", "read", action_severity="read")
    tool_registry = _tools_with(reader)
    child_answer = "On-topic, substantive child answer that should be delivered."

    async def script(prov: _ScriptedProvider, owl: str, dispatch, user_text):  # noqa: ANN001
        if owl == "reader_owl":
            return (child_answer, [])  # substantive ok → judge fires → errors → fail-open
        out = await dispatch("delegate_task", {"goal": "read the file", "to_owl": "reader_owl"})
        prov.parent_results.append(out)
        record = _record(out)
        return (str(record.get("result") or out), [
            {"name": "delegate_task", "args": {"to_owl": "reader_owl"}, "result": out},
        ])

    reg = OwlRegistry.with_default_secretary()
    reg.register(_specialist("reader_owl", "file-reader",
                             bounds=BoundsSpec(tools=frozenset({"read_file"}))))
    reg.register(_specialist("analyst", "data-analyst"))
    provider = _ScriptedProvider(script)
    # Patch the judge's provider.complete to raise (the agent loop uses
    # complete_with_tools, untouched; only the judge path calls provider.complete).
    monkeypatch.setattr(provider, "complete", _boom_complete)
    env = _build(tmp_db=tmp_db, provider=provider, owl_registry=reg, tool_registry=tool_registry)

    before_errors = judge_error_count()
    delivered = await _turn(env, "@analyst read the file")

    # --- fail-open: the ok was DELIVERED (not blocked) -------------------------
    record = _record(env.provider.parent_results[0])
    assert record["status"] == "ok", record
    assert "On-topic, substantive child answer" in str(record["result"]), record
    assert "On-topic, substantive child answer" in _unescape(delivered), delivered

    # --- the feature is NOT silently off: the error counter increased ----------
    assert judge_error_count() > before_errors, (
        f"judge_error_count did not increase: before={before_errors} now={judge_error_count()}"
    )
    assert env.governor.in_flight == 0


# ===========================================================================
# JOURNEY J5 — two DIFFERENT-goal delegations to the same owl: no false dedup
# ===========================================================================


@pytest.mark.asyncio
async def test_j5_two_different_goals_no_false_dedup(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parent makes TWO delegations with DIFFERENT goals to the SAME owl in one
    turn → both are REAL delegations (the D2 dedup memo keys on (owl, sub_task), so
    different goals do NOT collide). Both children genuinely ran.
    """
    monkeypatch.setattr(dt, "judge_relevance", _judge_relevant)

    reader = _RecordingTool("read_file", "read", action_severity="read")
    tool_registry = _tools_with(reader)

    async def script(prov: _ScriptedProvider, owl: str, dispatch, user_text):  # noqa: ANN001
        if owl == "reader_owl":
            # Each child run answers substantively (status ok). Distinguish the two
            # by echoing back the (different) sub-task (user_text) it received.
            return (f"answer for: {user_text}", [])
        # PARENT: two delegations, DIFFERENT goals, SAME target owl.
        out1 = await dispatch("delegate_task", {"goal": "read file A", "to_owl": "reader_owl"})
        out2 = await dispatch("delegate_task", {"goal": "read file B", "to_owl": "reader_owl"})
        prov.parent_results.extend([out1, out2])
        return ("done", [
            {"name": "delegate_task", "args": {"to_owl": "reader_owl"}, "result": out1},
            {"name": "delegate_task", "args": {"to_owl": "reader_owl"}, "result": out2},
        ])

    reg = OwlRegistry.with_default_secretary()
    reg.register(_specialist("reader_owl", "file-reader",
                             bounds=BoundsSpec(tools=frozenset({"read_file"}))))
    env = _build(tmp_db=tmp_db, provider=_ScriptedProvider(script),
                 owl_registry=reg, tool_registry=tool_registry)

    await _turn(env, "read file A and file B")

    # --- NO false dedup: the child ran TWICE (two distinct sub-tasks) -----------
    assert env.provider.owls_run.count("reader_owl") == 2, (
        f"two different-goal delegations were wrongly deduped: owls_run={env.provider.owls_run}"
    )
    assert len(env.provider.parent_results) == 2, env.provider.parent_results
    rec_a = _record(env.provider.parent_results[0])
    rec_b = _record(env.provider.parent_results[1])
    assert rec_a["status"] == "ok" and rec_b["status"] == "ok", (rec_a, rec_b)
    # Each carries its own distinct sub-task answer (proves two real, separate runs).
    assert "read file A" in str(rec_a["result"]), rec_a
    assert "read file B" in str(rec_b["result"]), rec_b
    assert env.governor.in_flight == 0


# ---------------------------------------------------------------------------
# Tool-registry helper: a MINIMAL registry (the REAL DelegateTaskTool + a
# recording tool of a chosen severity). Minimal (not with_defaults) so the
# recording write_file/read_file is the one the dispatch seam + _can_side_effect
# resolve — not shadowed by a real consequential tool of the same name.
# ---------------------------------------------------------------------------


def _tools_with(*extra: Tool) -> ToolRegistry:
    from stackowl.tools.agents.delegate_task import DelegateTaskTool

    reg = ToolRegistry()
    reg.register(DelegateTaskTool())
    for t in extra:
        reg.register(t)
    return reg


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])

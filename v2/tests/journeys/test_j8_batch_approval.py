"""J8 JOURNEY — "Batch-approved multi-step automation" (PRD §3, J8).

The business requirement, from the PRD User Journey J8:

  > **J8 — Batch-approved multi-step automation.** *"Run my morning routine."*
  > The owl plans N consequential actions and presents them as ONE BATCH
  > ("I will: 1… 2… 3 — approve all / reject") rather than N separate prompts.
  > Approve-all executes under a bounded, audited window.

  Business outcome: the user makes ONE batch decision (NOT N separate consent
  prompts); on approve-all the N actions execute (audited); the user is prompted
  EXACTLY ONCE.

This is NOT a per-tool smoke. It proves the USER's END-TO-END OUTCOME, driving a
single real inbound Telegram message through the GENUINE path
(TelegramChannelAdapter → GatewayScanner → AsyncioBackend pipeline →
execute._dispatch → ToolRegistry → REAL BatchApproveTool → REAL ClarifyGateway
suspend/resume → REAL CronjobTool / SendMessageTool with real side-effects) and
mocking ONLY the AI provider.

REAL (everything except the AI provider): the migrated ``DbPool`` (tmp_db); the
whole pipeline; the ``ToolRegistry`` + the REAL ``BatchApproveTool`` /
``CronjobTool`` / ``SendMessageTool``; the REAL ``ClarifyGateway`` (the in-process
suspend/resume registry whose inline keyboard is the ONE batch prompt) + the REAL
``TelegramClarifyResolver`` tap path (the user's "Approve all" tap resolves the
parked turn, exactly as the live Telegram loop does); the REAL ``JobScheduler`` +
``jobs`` table; the REAL ``AuditLogger`` (the bounded audited window); the REAL
``ProactiveDeliverer`` + ``NotificationRouter`` (send_message's outbound +
notification_log); and the Telegram adapter's inbound + outbound transport.

FAKED — ONLY the AI provider: a scripted, owl-aware secretary honoring the
ModelProvider contract (``name`` + a real ``CompletionResult`` from ``complete``
so triage runs genuinely). On its single ``complete_with_tools`` it calls the
REAL ``batch_approve`` tool with N=3 planned consequential actions and threads the
tool's structured outcome into its final reply. The Telegram bot HTTP transport is
faked in-process (``_FakeBot``) — transport, not a decision-maker.

Business-outcome assertions (NOT tool return-shapes):
  Scenario A (approve-all):
    1. EXACTLY ONE batch prompt reaches the user's chat — ONE inline keyboard
       listing all N actions with "Approve all" / "Reject", NOT N separate
       prompts (the load-bearing J8 assertion).
    2. The user taps "Approve all" via the REAL clarify-gateway round-trip.
    3. All N actions then EXECUTE with REAL audited side-effects: the two
       reminder jobs persist to the jobs table (and reload after a simulated
       restart) AND the send_message reaches the user's chat + writes a
       'delivered' notification_log row; the AuditLogger holds the batch grant +
       one row per action — derived from REAL effects, not constants.
  Scenario B (reject): the user taps "Reject" → NONE of the actions execute.
  Scenario C (non-interactive): interactive=False → no execution, structured
  needs-human (a background run never assumes approval).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from stackowl.audit.logger import AuditLogger
from stackowl.channels.registry import ChannelRegistry
from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.callbacks import CallbackRouter
from stackowl.channels.telegram.clarify import TelegramClarifyResolver
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.notification_settings import NotificationSettings
from stackowl.config.settings import Settings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner
from stackowl.interaction.clarify_gateway import ClarifyGateway
from stackowl.notifications.deliverer import ProactiveDeliverer
from stackowl.notifications.router import NotificationRouter
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.tools.interaction.batch_approve import BatchApproveTool
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 818181

# The N=3 planned consequential actions of the "morning routine". Two persist
# reminder jobs (real JobScheduler → jobs table); one sends a Telegram message
# (real ProactiveDeliverer → notification_log). Each produces an AUDITABLE
# side-effect the test asserts on AFTER the single batch approval.
_BRIEF_GOAL = "Send me my morning brief"
_STANDUP_GOAL = "Remind me about the team standup"
_BRIEF_SCHEDULE = "0 7 * * *"  # 07:00 daily
_STANDUP_SCHEDULE = "30 9 * * 1-5"  # 09:30 weekdays
_SEND_TEXT = "Good morning — your morning routine is armed."

_ACTIONS = [
    {"tool": "cronjob", "args": {"action": "create", "prompt": _BRIEF_GOAL, "schedule": _BRIEF_SCHEDULE},
     "summary": f"Schedule '{_BRIEF_GOAL}' at 07:00 daily"},
    {"tool": "cronjob", "args": {"action": "create", "prompt": _STANDUP_GOAL, "schedule": _STANDUP_SCHEDULE},
     "summary": f"Schedule '{_STANDUP_GOAL}' weekdays 09:30"},
    {"tool": "send_message", "args": {"action": "send", "text": _SEND_TEXT, "target": "telegram"},
     "summary": "Send a 'routine armed' confirmation over Telegram"},
]

_INTRO = "Here is your morning routine."

# The batch choices the BatchApproveTool presents. "Approve all" is index 0.
_APPROVE = "Approve all"
_REJECT = "Reject"


# --- FAKED #1: the Telegram bot HTTP transport (captures outbound in-process) ---


class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.answered: list[str] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):  # noqa: ANN001
        self.messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    async def answer_callback_query(self, callback_id, text=None):  # noqa: ANN001
        self.answered.append(callback_id)


class _FakeBotApp:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot

    def add_handler(self, handler: object) -> None:
        pass


# --- FAKED #2 (THE ONLY AI MOCK): the secretary owl's scripted provider ---------


class _ScriptedSecretary:
    """The ONLY mock: stands in for the secretary owl's LLM.

    On its single ``complete_with_tools`` it calls the REAL ``batch_approve`` tool
    with the N=3 planned consequential actions. In the interactive case the tool
    PARKS this coroutine on the clarify waiter until the user taps a batch button;
    the tool then returns its structured outcome and the model composes its final
    reply by THREADING that outcome — sliced from the REAL tool output, NOT a
    constant — so a broken batch (nothing executed / not threaded) cannot
    false-pass.
    """

    protocol = "anthropic"
    name = "scripted-secretary"

    def __init__(self) -> None:
        self.batch_out: str = ""
        self.final: str = ""

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, persistence_check=None, **kwargs,
    ):
        args = {"intro": _INTRO, "actions": _ACTIONS}
        self.batch_out = await tool_dispatcher("batch_approve", args)
        # Compose the final reply by threading the REAL batch outcome.
        if "succeeded" in self.batch_out:
            idx = self.batch_out.find("Executed")
            slice_ = self.batch_out[idx : idx + 40] if idx != -1 else self.batch_out[:40]
            self.final = f"Morning routine handled — {slice_}"
        else:
            self.final = f"Morning routine not run: {self.batch_out[:80]}"
        return (self.final, [{"name": "batch_approve", "args": args, "result": self.batch_out}])

    async def complete(self, *a, **k) -> CompletionResult:  # noqa: ANN002,ANN003
        return CompletionResult(
            content="", input_tokens=1, output_tokens=1, model="scripted",
            provider_name="scripted-secretary", duration_ms=0.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover — not on this path
        if False:
            yield ""


class _FakeProviderRegistry:
    def __init__(self, p: _ScriptedSecretary) -> None:
        self._p = p

    def get(self, name: str) -> _ScriptedSecretary:
        return self._p

    def get_by_tier(self, tier: str) -> _ScriptedSecretary:
        return self._p

    def get_with_cascade(self, tier: str) -> _ScriptedSecretary:
        return self._p


def _settings() -> Settings:
    return cast(
        Settings,
        SimpleNamespace(notifications=NotificationSettings(default_channel="telegram")),
    )


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    callback_router: CallbackRouter
    provider: _ScriptedSecretary
    gateway: ClarifyGateway
    audit: AuditLogger


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _clean_registry():  # noqa: ANN202
    ChannelRegistry.instance().reset()
    yield
    ChannelRegistry.instance().reset()


async def _build(tmp_db: DbPool, tmp_path: Path, *, timeout_s: float = 5.0) -> _Env:
    settings = _settings()

    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""
    ChannelRegistry.instance().register(adapter)

    # REAL clarify gateway — the in-process suspend/resume registry. The adapter
    # is the delivery surface for the ONE batch prompt (its inline keyboard).
    gateway = ClarifyGateway()
    gateway.register_adapter("telegram", adapter)

    # REAL AuditLogger over a tmp sqlite — the bounded audited window the batch
    # grant + each action write to.
    audit_path = tmp_path / "audit.db"
    conn = sqlite3.connect(audit_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit_log (audit_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "event_type TEXT NOT NULL, actor TEXT, target TEXT, timestamp REAL NOT NULL, "
        "details TEXT NOT NULL, integrity_hash TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()
    audit = AuditLogger(audit_path)

    # REAL callback router → the REAL clarify resolver (the "Approve all" tap path).
    router_cb = CallbackRouter(tmp_db, adapter)
    await router_cb.ensure_table()
    router_cb.register("clarify:", TelegramClarifyResolver(gateway).handle_callback)
    adapter.attach_callback_router(router_cb)

    # REAL S0 transport chokepoint for send_message. Clock pinned to noon UTC,
    # well outside any quiet window.
    notif_router = NotificationRouter(
        db=tmp_db, settings=settings,
        clock=lambda: datetime(2026, 5, 30, 12, 0, tzinfo=UTC),
    )
    deliverer = ProactiveDeliverer(
        router=notif_router, registry=ChannelRegistry.instance(), settings=settings
    )

    registry = ToolRegistry.with_defaults()  # REAL batch_approve/cronjob/send_message
    # Swap in a batch_approve with a SHORT park timeout so the reject/no-tap safety
    # net is bounded, not the 30-min default.
    registry.register(BatchApproveTool(timeout_s=timeout_s), replace=True)

    provider = _ScriptedSecretary()
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=registry,  # REAL batch_approve + the real actions
        # No per-action consent prompter wired: batch_approve is write-severity so
        # the dispatch gate passes it through; the inner cronjob/send_message run
        # via DIRECT execute (pre-consented), bypassing the per-action gate. This
        # is the J8 contract — ONE batch decision, not N consent prompts.
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
        clarify_gateway=gateway,  # REAL suspend/resume registry (the batch prompt)
        proactive_deliverer=deliverer,  # REAL deliverer on the S0 chokepoint
        audit_logger=audit,  # REAL audited window
        db_pool=tmp_db,  # REAL migrated DbPool — scheduler + router write here
    )
    return _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services),
        stream_registry=services.stream_registry,  # type: ignore[arg-type]
        callback_router=router_cb, provider=provider, gateway=gateway, audit=audit,
    )


async def _inbound(env: _Env, text: str) -> object:
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    return await env.adapter.receive()


def _keyboards(env: _Env) -> list[dict]:
    """All outbound messages that carry an inline keyboard (a consent/batch prompt)."""
    return [m for m in env.bot.messages if m["reply_markup"] is not None]


def _batch_cd(markup, choice_text: str) -> str:  # noqa: ANN001
    """Find the clarify callback_data for the button labelled ``choice_text``."""
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.text == choice_text:
                return btn.callback_data
    raise AssertionError(f"no {choice_text!r} button in batch keyboard")


async def _tap_batch(env: _Env, choice_text: str) -> None:
    """Wait for the ONE batch keyboard, then tap ``choice_text`` via the REAL router."""
    for _ in range(300):
        kbs = _keyboards(env)
        if kbs:
            cd = _batch_cd(kbs[-1]["reply_markup"], choice_text)
            update = SimpleNamespace(
                callback_query=SimpleNamespace(id=f"cb-{len(env.bot.answered)}", data=cd)
            )
            await env.callback_router.route(update, None)
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the batch prompt never appeared on Telegram")


async def _run_turn(env: _Env, text: str) -> tuple[asyncio.Task, asyncio.Task, str]:
    msg = await _inbound(env, text)
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text  # type: ignore[attr-defined]
    _writer, reader = env.stream_registry.create(msg.session_id)  # type: ignore[attr-defined]
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,  # type: ignore[attr-defined]
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",  # type: ignore[attr-defined]
        interactive=True,
    )
    run_task = asyncio.create_task(env.backend.run(state))
    send_task = asyncio.create_task(env.adapter.send(reader))
    return run_task, send_task, msg.session_id  # type: ignore[attr-defined]


# =============================================================================
# SCENARIO A — approve-all: ONE prompt → tap → N actions execute audited, once.
# =============================================================================


async def test_j8_run_morning_routine_one_batch_approval(
    tmp_db: DbPool, tmp_path: Path
) -> None:
    env = await _build(tmp_db, tmp_path)

    run_task, send_task, session_id = await _run_turn(env, "Run my morning routine.")

    # =================================================================
    # BUSINESS OUTCOME 1 — EXACTLY ONE batch prompt reaches the user: ONE inline
    # keyboard listing all N actions with Approve all / Reject — NOT N prompts.
    # This is the load-bearing J8 assertion (one batch decision, not N).
    # =================================================================
    for _ in range(300):
        if _keyboards(env):
            break
        await asyncio.sleep(0.01)
    kbs = _keyboards(env)
    assert len(kbs) == 1, (
        "BUSINESS OUTCOME 1 FAIL: expected EXACTLY ONE batch prompt (one keyboard), "
        f"got {len(kbs)}. N separate consent prompts would mean batch consent failed."
    )
    prompt = kbs[0]
    assert prompt["chat_id"] == USER_ID
    labels = [btn.text for row in prompt["reply_markup"].inline_keyboard for btn in row]
    assert labels == [_APPROVE, _REJECT], labels
    # The single prompt lists ALL N planned actions (the numbered plan).
    assert all(frag in prompt["text"] for frag in ("07:00 daily", "weekdays", "armed")), (
        f"the batch prompt did not list all N actions: {prompt['text']!r}"
    )
    # The turn is genuinely SUSPENDED while it waits for the batch decision.
    assert not run_task.done(), "BUSINESS OUTCOME 1 FAIL: the turn did not suspend on the batch prompt."

    # =================================================================
    # BUSINESS OUTCOME 2 — the user taps "Approve all" ONCE via the REAL
    # clarify-gateway round-trip (the live tap path), waking the parked turn.
    # =================================================================
    await _tap_batch(env, _APPROVE)
    await asyncio.wait_for(run_task, timeout=5.0)
    await asyncio.wait_for(send_task, timeout=5.0)
    env.stream_registry.remove(session_id)

    # The user was prompted EXACTLY ONCE (still one keyboard after the whole turn).
    assert len(_keyboards(env)) == 1, (
        "BUSINESS OUTCOME 2 FAIL: more than ONE prompt was delivered — the user was "
        f"asked {len(_keyboards(env))} times, not once."
    )

    # =================================================================
    # BUSINESS OUTCOME 3 — all N actions EXECUTED with REAL audited side-effects.
    # =================================================================
    # The batch tool reported all three ran (threaded into the final reply).
    assert "3 succeeded" in env.provider.batch_out, (
        f"BUSINESS OUTCOME 3 FAIL: the batch did not execute all 3 actions: {env.provider.batch_out!r}"
    )

    # (a) The two reminder jobs PERSISTED and reload after a simulated restart.
    rows = await tmp_db.fetch_all("SELECT job_id, handler_name, params FROM jobs", ())
    goals = {json.loads(r["params"]).get("goal") for r in rows}
    assert _BRIEF_GOAL in goals and _STANDUP_GOAL in goals, (
        f"BUSINESS OUTCOME 3 FAIL: the reminder jobs did not persist. Goals: {goals}"
    )
    fresh = JobScheduler(db=tmp_db)
    reloaded_goals = {j.params.get("goal") for j in await fresh.list_jobs()}
    assert _BRIEF_GOAL in reloaded_goals and _STANDUP_GOAL in reloaded_goals, (
        f"BUSINESS OUTCOME 3 FAIL: a FRESH scheduler did not reload the jobs: {reloaded_goals}"
    )

    # (b) The send_message reached the user's chat (keyboard-less) AND wrote a
    #     'delivered' notification_log row.
    delivered_msgs = [
        m for m in env.bot.messages
        if m["chat_id"] == USER_ID and m["reply_markup"] is None and "armed" in m["text"]
    ]
    assert delivered_msgs, (
        "BUSINESS OUTCOME 3 FAIL: the send_message confirmation never reached the user. "
        f"Outbound: {[m['text'] for m in env.bot.messages]}"
    )
    log_rows = await tmp_db.fetch_all("SELECT channel, delivery_status FROM notification_log", ())
    assert any(r["channel"] == "telegram" and r["delivery_status"] == "delivered" for r in log_rows), (
        f"BUSINESS OUTCOME 3 FAIL: no 'delivered' notification_log row. Rows: {log_rows}"
    )

    # (c) The AUDITED window: the batch grant + one row per action (the bounded,
    #     audited execution the PRD requires), from the REAL AuditLogger.
    audit_rows = env.audit.tail(50)
    grants = [r for r in audit_rows if r["event_type"] == "batch_approval.granted"]
    actions = [r for r in audit_rows if r["event_type"] == "batch_approval.action"]
    assert len(grants) == 1, f"BUSINESS OUTCOME 3 FAIL: expected ONE batch grant, got {grants}"
    assert len(actions) == 3, (
        f"BUSINESS OUTCOME 3 FAIL: expected 3 audited actions, got {len(actions)}: {actions}"
    )
    assert not any(r["event_type"] == "batch_approval.rejected" for r in audit_rows)


# =============================================================================
# SCENARIO B — reject: tap "Reject" → NONE of the actions execute.
# =============================================================================


async def test_j8_reject_executes_nothing(tmp_db: DbPool, tmp_path: Path) -> None:
    env = await _build(tmp_db, tmp_path)
    run_task, send_task, session_id = await _run_turn(env, "Run my morning routine.")

    await _tap_batch(env, _REJECT)
    await asyncio.wait_for(run_task, timeout=5.0)
    await asyncio.wait_for(send_task, timeout=5.0)
    env.stream_registry.remove(session_id)

    # Exactly ONE prompt was still shown (the batch), and NOTHING executed.
    assert len(_keyboards(env)) == 1
    jobs = await tmp_db.fetch_all("SELECT params FROM jobs", ())
    goals = {json.loads(j["params"]).get("goal") for j in jobs}
    assert _BRIEF_GOAL not in goals and _STANDUP_GOAL not in goals, (
        f"REJECT FAIL: a reminder job was scheduled despite rejection. Goals: {goals}"
    )
    # No 'routine armed' message was delivered.
    assert not [m for m in env.bot.messages if m["reply_markup"] is None and "armed" in m["text"]], (
        "REJECT FAIL: the send_message ran despite rejection."
    )
    # Audited as rejected, never granted.
    audit_rows = env.audit.tail(50)
    assert any(r["event_type"] == "batch_approval.rejected" for r in audit_rows)
    assert not any(r["event_type"] == "batch_approval.granted" for r in audit_rows)


# =============================================================================
# SCENARIO C — non-interactive: no execution, structured needs-human.
# =============================================================================


async def test_j8_non_interactive_fail_closed(tmp_db: DbPool, tmp_path: Path) -> None:
    env = await _build(tmp_db, tmp_path)

    state = PipelineState(
        trace_id="t-cron-j8", session_id=str(USER_ID), input_text="Run my morning routine.",
        channel="telegram", owl_name="secretary", pipeline_step="start",
        interactive=False,
    )
    await asyncio.wait_for(env.backend.run(state), timeout=3.0)

    # The batch tool returned the needs-human record instead of parking.
    assert "non-interactive" in env.provider.batch_out.lower(), (
        "NON-INTERACTIVE FAIL: batch_approve did not fail closed — a background run "
        f"would assume approval. Tool output: {env.provider.batch_out!r}"
    )
    # Nothing was prompted, nothing parked, nothing executed.
    assert len(_keyboards(env)) == 0
    assert env.gateway.try_resolve(str(USER_ID), "telegram", "x") is None
    jobs = await tmp_db.fetch_all("SELECT params FROM jobs", ())
    goals = {json.loads(j["params"]).get("goal") for j in jobs}
    assert _BRIEF_GOAL not in goals and _STANDUP_GOAL not in goals, (
        f"NON-INTERACTIVE FAIL: an action executed without approval. Goals: {goals}"
    )
    assert env.provider.final, "NON-INTERACTIVE FAIL: the run produced no reply (it may have hung)."

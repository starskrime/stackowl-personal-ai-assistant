"""J6 JOURNEY — "Ambiguous request triggers pause-and-resume" (PRD §3, J6).

The business requirement, verbatim from the PRD User Journey J6:

  > **J6 — Ambiguous request triggers pause-and-resume.** *"Set that up for me."*
  > The owl detects insufficient grounding and calls ``clarify``, which SUSPENDS
  > the pipeline turn, emits a question to the originating channel, and RESUMES
  > with the answer threaded into the same PipelineState. Interactivity-gated: a
  > cron/heartbeat (non-interactive) run does NOT pause — it self-heals (proceeds
  > with a logged assumption or aborts with a structured "needs human input").

THE headline business outcome: an ambiguous request → the owl ASKS the user a
clarifying question (delivered to their channel) → the user ANSWERS → the SAME
turn resumes and proceeds USING the answer. AND: a non-interactive (cron) run
does NOT hang waiting on a question nobody can answer — it self-heals.

This is NOT a per-tool smoke. It proves the USER's END-TO-END OUTCOME across the
E5 clarify keystone, driving real inbound Telegram messages through the GENUINE
path (TelegramChannelAdapter → GatewayScanner → REAL ClarifyPump →
AsyncioBackend pipeline → execute._dispatch → ToolRegistry → REAL ClarifyTool →
REAL ClarifyGateway suspend/resume) and mocking ONLY the AI provider.

REAL (everything except the AI provider): the whole pipeline, the REAL
``ToolRegistry`` + ``ClarifyTool``, the REAL ``ClarifyGateway`` (the in-process
suspend/resume registry — an ``asyncio.Event`` parks the turn mid-dispatch and
the answer wakes it IN THE SAME TURN), the REAL ``ClarifyPump.resolve_or_rewrite``
(the channel-loop interceptor that routes the user's typed reply to the parked
turn's waiter — exactly as the live Telegram loop does), the REAL
``TraceContext`` interactivity gate (``state.interactive`` flows into the
TraceContext the clarify tool reads), the REAL ``StreamRegistry``, and the
Telegram adapter's inbound (``_handle_update`` → ``receive``) + outbound
(``send``) + ``send_clarify`` transport.

FAKED — ONLY the AI provider: a scripted, owl-aware secretary that, on the FIRST
``complete_with_tools`` call of the turn, calls the REAL ``clarify`` tool (which
parks the coroutine), and on RESUME composes its final reply by THREADING the
answer the tool returned (the answer is sliced out of the real tool output — NOT
a constant — so a broken resume that fails to thread the answer fails the test).
The Telegram bot HTTP transport is faked in-process (``_FakeBot``) — transport,
not a decision-maker.

Business-outcome assertions (NOT tool return-shapes):
  Scenario A (interactive pause→resume):
    1. The clarifying QUESTION is DELIVERED to the user's Telegram chat, and the
       turn is genuinely SUSPENDED (the run has not finished) while it waits.
    2. After the user ANSWERS (a real inbound reply driven through the REAL
       pump), the SAME turn RESUMES and the owl's final reply REFLECTS the
       answer ("morning brief") — derived from the REAL resumed turn (the tool's
       answer frame), proving the answer was threaded into the continuation.
  Scenario B (non-interactive self-heals):
    3. The SAME clarify call under a NON-interactive (cron-style) context does
       NOT park/hang — it returns the structured non-interactive sentinel and
       the run completes. A background run never deadlocks on a question nobody
       can answer.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.clarify_pump import ClarifyPump
from stackowl.gateway.scanner import GatewayScanner
from stackowl.interaction.clarify_gateway import ClarifyGateway
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult
from stackowl.tools.interaction.clarify import ClarifyTool
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 636363

# The ambiguous request that triggers J6 — there is nothing in it to ground the
# owl, so a real owl asks WHAT to set up.
_AMBIGUOUS = "Set that up for me."
# The clarifying question the scripted owl asks via the REAL clarify tool.
_QUESTION = "Set up WHAT — the morning brief or the backup job?"
_CHOICES = ("the morning brief", "the backup job")
# The user's answer (driven as a real inbound reply). It picks the morning brief
# BUT carries a distinctive token ("daily 7am digest") that the user alone
# supplies — it appears NOWHERE in the question or choices. The final reply must
# REFLECT this token, proving the same turn resumed with THE ANSWER (not the
# question) threaded into the continuation.
#
# WHY a token absent from the question: the clarify tool's answer frame ECHOES
# the question alongside the answer ("The user answered ({question!r}): {answer}").
# If we asserted on a phrase that also lives in the question, a WRONG answer would
# STILL surface that phrase (it leaks in via the echoed question) and the test
# would false-pass. Asserting on a token unique to the ANSWER makes the assertion
# load-bearing on the resume actually threading the user's reply. (Verified: with
# this token a sabotaged answer fails the test; the question-shared phrasing did
# not — that was the harness bug this comment guards against.)
_ANSWER = "the morning brief — my daily 7am digest"
# A distinctive token that exists in the final reply ONLY if the user's ANSWER
# was threaded back through the resumed turn. Absent from _QUESTION/_CHOICES.
_ANSWER_PHRASE = "daily 7am digest"


# --- FAKED transport: the Telegram bot HTTP layer (in-process capture) ----------


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


# --- FAKED (THE ONLY AI MOCK): the secretary owl's scripted provider ------------


class _ScriptedSecretary:
    """The ONLY mock: stands in for the secretary owl's LLM.

    On the FIRST (and only) ``complete_with_tools`` of the turn it calls the REAL
    ``clarify`` tool. In the interactive case the tool PARKS this coroutine inside
    the dispatch until the user's reply wakes it; the tool then returns the answer
    frame and the model COMPOSES its final reply by THREADING the answer it got —
    sliced out of the REAL tool output, NOT a constant — so a broken resume (the
    answer never reaching the model) cannot false-pass.
    """

    protocol = "anthropic"
    # Honor the ModelProvider contract (base.py: `name` property) so the real
    # `triage` step (router.py reads `provider.name`) runs genuinely instead of
    # silently erroring + being swallowed by the backend's per-step self-heal.
    name = "scripted-secretary"

    def __init__(self) -> None:
        self.clarify_out: str = ""
        self.final: str = ""

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, persistence_check=None, **kwargs,
    ):
        # The request is ambiguous → ask the user (REAL clarify tool). In the
        # interactive case this PARKS here until the answer wakes the waiter.
        args = {"question": _QUESTION, "choices": list(_CHOICES)}
        self.clarify_out = await tool_dispatcher("clarify", args)

        # Compose the final reply by THREADING the answer the tool returned. The
        # phrase is sliced out of the REAL tool output (the answer frame the
        # gateway wrote), so the reply can only contain it if the turn genuinely
        # resumed with the user's answer in hand. No constant rescue.
        if _ANSWER_PHRASE in self.clarify_out:
            idx = self.clarify_out.find(_ANSWER_PHRASE)
            threaded = self.clarify_out[idx : idx + len(_ANSWER_PHRASE)]
            self.final = (
                f"Got it — setting up the {threaded} for you now."
            )
        else:
            # Resume did not thread the answer (or did not happen) → an honest
            # reply that lacks the phrase, so assertion 2 fails for real.
            self.final = (
                "I could not determine what to set up from your answer "
                f"(tool said: {self.clarify_out!r})."
            )
        return (self.final, [{"name": "clarify", "args": args, "result": self.clarify_out}])

    async def complete(self, *a, **k) -> CompletionResult:  # noqa: ANN002,ANN003
        # The real triage step CALLS complete() (router reads .input_tokens), so
        # honor the ModelProvider result contract — return a real CompletionResult
        # so triage runs genuinely instead of crashing + being swallowed.
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


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: _ScriptedSecretary
    gateway: ClarifyGateway
    pump: ClarifyPump


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _build(*, clarify_timeout_s: float) -> _Env:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    provider = _ScriptedSecretary()
    # REAL clarify gateway — the in-process suspend/resume registry. The adapter
    # is the delivery surface for the parked question.
    gateway = ClarifyGateway()
    gateway.register_adapter("telegram", adapter)

    registry = ToolRegistry.with_defaults()  # REAL clarify tool registered here
    # Swap in a clarify tool with a SHORT park timeout so Scenario A's timeout
    # safety net (if the answer never wakes it) is bounded, not the 30-min default.
    registry.register(ClarifyTool(timeout_s=clarify_timeout_s), replace=True)

    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=registry,  # REAL clarify
        consent_gate=ConsequentialActionGate(),  # clarify is read-severity; no gate fires
        stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
        clarify_gateway=gateway,  # REAL suspend/resume registry
    )
    # The REAL pump — the channel-loop interceptor that routes a typed reply to
    # the parked turn's waiter (exactly what the live Telegram loop runs).
    pump = ClarifyPump(gateway, services.stream_registry)  # type: ignore[arg-type]
    return _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services),
        stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider, gateway=gateway, pump=pump,
    )


async def _inbound(env: _Env, text: str) -> object:
    """Drive a REAL inbound Telegram message through the adapter; return the msg."""
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    return await env.adapter.receive()


async def _wait_until(predicate, *, tries: int = 300) -> bool:  # noqa: ANN001
    for _ in range(tries):
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


async def test_j6_ambiguous_request_pauses_then_resumes_with_the_answer() -> None:
    """SCENARIO A — interactive: ambiguous request → ask → answer → same turn resumes."""
    env = _build(clarify_timeout_s=5.0)

    # The user sends the ambiguous request — AS THE USER, over Telegram.
    msg = await _inbound(env, _AMBIGUOUS)
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text  # type: ignore[attr-defined]

    # Real stream slot for this session, then START the turn. interactive=True →
    # the TraceContext the clarify tool reads marks a human is present to answer,
    # so clarify PARKS (does not return the non-interactive sentinel).
    _writer, reader = env.stream_registry.create(msg.trace_id)  # type: ignore[attr-defined]
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,  # type: ignore[attr-defined]
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",  # type: ignore[attr-defined]
        interactive=True,
    )
    run_task = asyncio.create_task(env.backend.run(state))
    # Drain the outbound stream concurrently (the live loop does this in its own
    # task so the receive loop stays free while the turn is parked).
    send_task = asyncio.create_task(env.adapter.send(reader))

    # =================================================================
    # BUSINESS OUTCOME 1 — the clarifying QUESTION is DELIVERED to the user's
    # chat AND the turn is genuinely SUSPENDED (it has not finished) while it
    # waits for the answer. This is the SUSPEND half of pause-and-resume.
    # =================================================================
    delivered = await _wait_until(
        lambda: any("morning brief or the backup job" in m["text"] for m in env.bot.messages)
    )
    assert delivered, (
        "BUSINESS OUTCOME 1 FAIL: the clarifying question was never delivered to "
        f"the user's Telegram chat. Outbound: {[m['text'] for m in env.bot.messages]!r}"
    )
    assert not run_task.done(), (
        "BUSINESS OUTCOME 1 FAIL: the turn did NOT suspend — it finished without "
        "waiting for the user's answer. Pause-and-resume requires the turn to park."
    )
    # The question reached the ASKING user's chat (not some other chat).
    q_msgs = [m for m in env.bot.messages if "morning brief or the backup job" in m["text"]]
    assert q_msgs[0]["chat_id"] == USER_ID, q_msgs

    # =================================================================
    # The user ANSWERS — a REAL inbound reply, routed through the REAL pump
    # (resolve_or_rewrite), exactly as the live Telegram loop does. A blocking
    # resolve wakes the parked waiter IN THE SAME TURN and the pump reports it
    # consumed the message (the loop must start NO new turn).
    # =================================================================
    answer_msg = await _inbound(env, _ANSWER)
    answer_decision = env.scanner.scan(answer_msg)
    consumed, _rewritten = await env.pump.resolve_or_rewrite(
        session_id=answer_msg.session_id,  # type: ignore[attr-defined]
        channel=answer_msg.channel,  # type: ignore[attr-defined]
        route=answer_decision.route,
        target=answer_decision.target,
        input_text=answer_msg.text,  # type: ignore[attr-defined]
    )
    assert consumed, (
        "RESUME WIRING FAIL: the pump did NOT resume the parked turn on the user's "
        "answer (consumed=False). The same-turn resume edge is unwired."
    )

    # The parked turn must now RESUME and finish.
    await asyncio.wait_for(run_task, timeout=5.0)
    await asyncio.wait_for(send_task, timeout=5.0)
    env.stream_registry.remove(msg.trace_id)  # type: ignore[attr-defined]

    # =================================================================
    # BUSINESS OUTCOME 2 — the SAME turn resumed and the owl PROCEEDED USING the
    # answer: the final reply REFLECTS "morning brief". This is derived from the
    # REAL resumed turn (the tool's answer frame the model threaded), NOT a
    # constant — a broken resume that fails to thread the answer cannot pass.
    # =================================================================
    # The clarify tool returned the user's ANSWER (the answered frame), proving
    # the wait_for_answer waiter was woken with the real reply.
    assert _ANSWER_PHRASE in env.provider.clarify_out, (
        "BUSINESS OUTCOME 2 FAIL: the clarify tool did not return the user's answer "
        f"to the model — resume did not thread it. Tool output: {env.provider.clarify_out!r}"
    )
    # The final reply the user sees reflects the answer (the resumed continuation).
    delivered_text = "\n".join(
        m["text"] for m in env.bot.messages
        if m["chat_id"] == USER_ID and m["reply_markup"] is None
    )
    # The adapter MarkdownV2-escapes punctuation on the way out; compare on the
    # escape-stripped text (the J1/J2 pattern).
    assert _ANSWER_PHRASE in delivered_text.replace("\\", ""), (
        "BUSINESS OUTCOME 2 FAIL: the resumed turn's final reply did NOT reflect the "
        f"user's answer. Delivered: {delivered_text!r} | final={env.provider.final!r}"
    )


async def test_j6_non_interactive_run_does_not_hang_self_heals() -> None:
    """SCENARIO B — non-interactive (cron): clarify does NOT park; the run completes."""
    env = _build(clarify_timeout_s=5.0)

    # Drive the SAME ambiguous request, but in a NON-interactive context
    # (interactive=False — a cron/heartbeat/goal_execution-style run). Nobody can
    # answer; the turn must NOT park. We bound the whole run with a tight timeout:
    # if it parked, the (much longer) clarify park would blow this bound and fail
    # honestly — proving "does not hang".
    state = PipelineState(
        trace_id="t-cron-j6", session_id=str(USER_ID), input_text=_AMBIGUOUS,
        channel="telegram", owl_name="secretary", pipeline_step="start",
        interactive=False,
    )
    await asyncio.wait_for(env.backend.run(state), timeout=3.0)

    # =================================================================
    # BUSINESS OUTCOME 3 — the cron run SELF-HEALED: clarify returned the
    # structured non-interactive sentinel (ABORT-or-assume contract) instead of
    # parking, so the run completed. A background run never deadlocks on a
    # question nobody can answer.
    # =================================================================
    assert "non-interactive" in env.provider.clarify_out.lower(), (
        "BUSINESS OUTCOME 3 FAIL: under a non-interactive (cron) context clarify did "
        "NOT return the non-interactive sentinel — the background run would hang on a "
        f"question nobody can answer. Tool output: {env.provider.clarify_out!r}"
    )
    # Nothing was parked: no pending clarify lingers for this session/channel.
    assert env.gateway.try_resolve(str(USER_ID), "telegram", "whatever") is None, (
        "BUSINESS OUTCOME 3 FAIL: a non-interactive run registered a pending clarify — "
        "it would park a waiter nobody will ever resolve."
    )
    # The run produced a final reply (it completed, did not deadlock).
    assert env.provider.final, "BUSINESS OUTCOME 3 FAIL: the non-interactive run produced no reply"

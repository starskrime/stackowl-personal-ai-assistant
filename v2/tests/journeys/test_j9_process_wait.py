"""J9 JOURNEY — "Run a longer task, WAIT for it, then read its output" (E9, S2).

The business requirement, in user terms:

  > The owl runs a longer background task, WAITS for it to finish (without
  > busy-polling), then reads its output and reports it back to me.

THE headline business outcome: the user's final reply contains the REAL output of
a background task that the owl (a) started via ``process``, (b) awaited via
``wait for_process`` (blocking ONCE until it exited — not looping ``process poll``),
and (c) read back via ``process log``. start → wait → log chained end-to-end
through the GENUINE gateway path, mocking ONLY the AI provider.

This is NOT a per-tool smoke. It drives a real inbound Telegram message through
the GENUINE path (TelegramChannelAdapter → GatewayScanner → AsyncioBackend
pipeline → execute._dispatch → ToolRegistry → REAL ProcessTool + REAL WaitTool →
a REAL ``ProcessRegistry`` spawning a REAL OS subprocess). The deterministic child
(``sys.executable -c "...print 0..4..."``) prints five known lines over ~0.25s, so
the assertions are DERIVED from the child's real stdout — not constants.

REAL (everything except the AI provider): the whole pipeline, the REAL
``ToolRegistry`` + ``ProcessTool`` + ``WaitTool``, the REAL ``ProcessRegistry``
(spawns/supervises/reaps the real subprocess), the REAL ``TraceContext`` session
scoping (the wait polls the registry under the caller's session), and the Telegram
adapter inbound (``_handle_update`` → ``receive``) + outbound (``send``).

FAKED — ONLY the AI provider: a scripted, owl-aware secretary that, on its single
``complete_with_tools`` of the turn, emits the THREE tool calls IN ORDER —
``process start`` (capturing the REAL returned ``process_id``), then ``wait
for_process=<that id>`` (asserting the wait reports the process genuinely exited),
then ``process log`` (slicing the REAL captured stdout) — and composes its final
reply by THREADING that real stdout. The Telegram bot HTTP transport is faked
in-process (``_FakeBot``).

NO-BUSY-POLL proof: the provider spies on ``ProcessRegistry.poll`` and counts the
calls the wait makes. A busy spin would call it thousands of times; the wait is
interval-paced (``WAIT_POLL_INTERVAL_SECONDS``), so the count is a small handful.
The journey asserts a tight upper bound — a regression to busy-polling fails it.

FAIL-WHEN-UNWIRED proof: the REAL ``ProcessRegistry`` is injected onto
``StepServices.process_registry``. If it were NOT injected, ``process start``
self-heals to a structured "unavailable" (no ``process_id``), the wait then has
nothing to await, ``process log`` returns nothing, and the child's stdout never
reaches the user's reply — every business-outcome assertion fails. (The dev-review
check: drop ``process_registry=`` from the StepServices below and the journey goes
red.)
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import GatewayScanner
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.process.registry import ProcessRegistry
from stackowl.providers.base import CompletionResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 919191

# A bounded, deterministic, cross-platform child: prints five KNOWN lines 0..4 over
# ~0.25s, then exits 0. Finite (so wait actually sees a terminal state) and its
# stdout is fully predictable (so the assertions derive from real output).
_CHILD = (
    "import time\n"
    "for i in range(5):\n"
    "    print(i, flush=True)\n"
    "    time.sleep(0.05)\n"
)
_EXPECTED_LINES = ["0", "1", "2", "3", "4"]


def _child_argv() -> list[str]:
    return [sys.executable, "-u", "-c", _CHILD]


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
    """The ONLY mock: the secretary owl's LLM, scripted to run the journey.

    On its single ``complete_with_tools`` it emits the three tool calls in order,
    THREADING real outputs between them: it captures the real ``process_id`` from
    ``process start``, awaits it with ``wait for_process``, asserts the wait says
    the process exited, then reads the REAL captured stdout via ``process log`` and
    composes the final reply from those real lines (no constants).
    """

    protocol = "anthropic"
    # Honor the ModelProvider contract so the real triage step (router reads
    # provider.name + calls complete()) runs genuinely, not swallowed.
    name = "scripted-secretary"

    def __init__(self) -> None:
        self.start_out: str = ""
        self.wait_out: str = ""
        self.log_out: str = ""
        self.process_id: str = ""
        self.final: str = ""

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, persistence_check=None, **kwargs,
    ):
        calls: list[dict] = []

        # 1) START a bounded background task → capture the REAL process_id.
        self.start_out = await tool_dispatcher(
            "process", {"action": "start", "command": _child_argv()}
        )
        calls.append({"name": "process", "args": {"action": "start"}, "result": self.start_out})
        self.process_id = json.loads(self.start_out).get("process_id", "")

        # 2) WAIT for it to finish — the CORRECT way to await a process (one call,
        # blocks until it exits). The wait honors session scoping + the injected
        # deadline; here it returns satisfied=true because the child really exits.
        self.wait_out = await tool_dispatcher(
            "wait", {"for_process": self.process_id, "timeout": 30.0}
        )
        calls.append({"name": "wait", "args": {"for_process": self.process_id}, "result": self.wait_out})

        # 3) READ the captured output and THREAD the real stdout into the reply.
        self.log_out = await tool_dispatcher(
            "process", {"action": "log", "process_id": self.process_id, "stream": "stdout"}
        )
        calls.append({"name": "process", "args": {"action": "log"}, "result": self.log_out})

        stdout = json.loads(self.log_out).get("stdout", "")
        # Final reply built from the REAL captured stdout lines (not a constant).
        self.final = f"The task finished. Output:\n{stdout.strip()}"
        return (self.final, calls)

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


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: _ScriptedSecretary
    registry: ProcessRegistry
    poll_calls: dict


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch) -> None:
    # Keep the registry checkpoint out of the real ~/.stackowl/.
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _build() -> _Env:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    provider = _ScriptedSecretary()

    # REAL ProcessRegistry — spawns/supervises/reaps the real subprocess. Spy on
    # poll() so the journey can prove the wait did NOT busy-spin.
    registry = ProcessRegistry()
    poll_calls = {"n": 0}
    real_poll = registry.poll

    async def _counting_poll(process_id, session_id=None):  # noqa: ANN001, ANN202
        poll_calls["n"] += 1
        return await real_poll(process_id, session_id)

    registry.poll = _counting_poll  # type: ignore[assignment]

    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),  # REAL process + wait tools
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
        process_registry=registry,  # REAL substrate (drop this → journey goes red)
    )
    return _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services),
        stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider, registry=registry, poll_calls=poll_calls,
    )


async def _inbound(env: _Env, text: str) -> object:
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    return await env.adapter.receive()


async def test_j9_run_wait_then_read_output_through_the_gateway() -> None:
    """The owl runs a longer task, WAITS for it, then reads its output — and the
    user's final reply contains that REAL output. start → wait → log, end-to-end."""
    env = _build()

    # The user asks — AS THE USER, over Telegram.
    msg = await _inbound(env, "Run the counter task and tell me what it printed.")
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text  # type: ignore[attr-defined]

    _writer, reader = env.stream_registry.create(msg.session_id)  # type: ignore[attr-defined]
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,  # type: ignore[attr-defined]
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",  # type: ignore[attr-defined]
    )
    run_task = asyncio.create_task(env.backend.run(state))
    send_task = asyncio.create_task(env.adapter.send(reader))
    await asyncio.wait_for(run_task, timeout=20.0)
    await asyncio.wait_for(send_task, timeout=5.0)
    env.stream_registry.remove(msg.session_id)  # type: ignore[attr-defined]

    # =================================================================
    # OUTCOME 1 — the task really STARTED through the gateway: process start
    # returned a real process_id (proving the REAL ProcessRegistry was wired;
    # unwired → "unavailable" + no id, and everything downstream collapses).
    # =================================================================
    assert env.provider.process_id, (
        "OUTCOME 1 FAIL: process start returned no process_id — the registry was "
        f"not wired/reached. start_out={env.provider.start_out!r}"
    )

    # =================================================================
    # OUTCOME 2 — the WAIT genuinely awaited the process to EXIT (satisfied=true,
    # derived from the real tool output — not a constant). The process truly
    # terminated; the wait blocked ONCE until it did.
    # =================================================================
    wait_data = json.loads(env.provider.wait_out)
    assert wait_data["mode"] == "process", wait_data
    assert wait_data["process_id"] == env.provider.process_id, wait_data
    assert wait_data["satisfied"] is True, (
        f"OUTCOME 2 FAIL: wait did not observe the process exit. wait_out={wait_data!r}"
    )
    assert wait_data["status"] == "exited", wait_data
    assert wait_data["exit_code"] == 0, wait_data

    # =================================================================
    # OUTCOME 3 — NO BUSY-POLL: the wait polled the registry only a SMALL bounded
    # number of times (interval-paced), not a tight spin. ~0.25s / 0.5s interval is
    # a couple of polls; a busy spin would be in the thousands.
    # =================================================================
    n = env.poll_calls["n"]
    assert 1 <= n <= 12, (
        f"OUTCOME 3 FAIL: wait polled the registry {n} times — expected a small "
        "interval-paced handful, not a busy spin."
    )

    # =================================================================
    # OUTCOME 4 — the user's final reply contains the task's REAL output: the five
    # known printed lines 0..4 reach the user over Telegram. This proves start →
    # wait → log chained end-to-end (the output only exists if the process ran AND
    # the wait let it finish AND log read its real captured stdout).
    # =================================================================
    delivered = "\n".join(m["text"] for m in env.bot.messages if m["chat_id"] == USER_ID)
    # The adapter MarkdownV2-escapes punctuation; compare on escape-stripped text.
    clean = delivered.replace("\\", "")
    for line in _EXPECTED_LINES:
        assert line in clean, (
            f"OUTCOME 4 FAIL: the child's real output line {line!r} did not reach "
            f"the user. Delivered: {delivered!r}"
        )
    # And the log tool genuinely returned that stdout (the source of the reply).
    assert all(line in json.loads(env.provider.log_out)["stdout"] for line in _EXPECTED_LINES), (
        f"OUTCOME 4 FAIL: process log did not capture the child's stdout. log_out={env.provider.log_out!r}"
    )
    assert env.bot.messages, "no outbound Telegram message"
    assert env.bot.messages[-1]["chat_id"] == USER_ID

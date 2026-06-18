"""J11 JOURNEY — "Run code and tell me the result" (E11, S5).

The business requirement, in user terms:

  > The owl writes a snippet, RUNS it in a sandbox (after I approve), and tells me
  > the real result. The code never touches my host; if it tries to reach the
  > network without asking, it can't; if I decline, nothing runs.

THE headline outcome: the user's final reply contains the REAL output ("4") of code
the owl ran in a REAL bubblewrap sandbox — driven end-to-end through the GENUINE
gateway path, mocking ONLY the AI provider, the bot transport, and the consent
prompter (auto-approve). The other outcomes assert the load-bearing SAFETY rails:
network is genuinely denied; a delegated child is REFUSED at dispatch (GAP-B); an
unwired/unavailable selector returns "unavailable" and runs NOTHING on the host;
the consent prompt SHOWED the actual code (GAP-A); a DENY runs nothing.

REAL (everything except the AI + transport + consent UX): the whole pipeline, the
REAL ``ToolRegistry`` + ``ExecuteCodeTool``, the REAL ``SandboxSelector`` and the
REAL ``BwrapSandbox`` (spawns a real rootless+cgroup sandbox running real python).

FAKED:
  * the AI provider (a scripted secretary that calls ``execute_code`` and threads
    the real stdout into its reply),
  * the Telegram bot HTTP transport (``_FakeBot``),
  * the consent prompter (``_RecordingPrompter`` — auto-approves or denies AND
    records the ``summary`` it was shown, so the journey can assert GAP-A).

FAIL-WHEN-UNWIRED proof: the REAL ``SandboxSelector`` is injected onto
``StepServices.sandbox_selector``. Drop it (``test_unavailable_…``) and
``execute_code`` self-heals to "unavailable" with ZERO host execution — every
output assertion that depends on a real run goes red. The bwrap viability is probed
once; if the host cannot run bwrap the live-output + network tests SKIP honestly,
but the consent/child-exclusion/unavailable assertions (which need no live sandbox)
still run.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, field
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
from stackowl.providers.base import CompletionResult
from stackowl.sandbox.bwrap import BwrapSandbox
from stackowl.sandbox.capability import SandboxCapability
from stackowl.sandbox.cgroup import CgroupRecipe
from stackowl.sandbox.docker import DockerSandbox
from stackowl.sandbox.selector import SandboxSelector
from stackowl.tools.consent import ConsentPolicy, ConsentRequest, ConsentScope, RoutingPrompter
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 717171


def _bwrap_live() -> bool:
    """True iff a REAL bwrap run can isolate AND enforce caps on this host."""
    if shutil.which("bwrap") is None:
        return False
    probe = SandboxCapability.probe()
    if not probe.bwrap_viable:
        return False
    cg_ok, _ = CgroupRecipe.delegation_available()
    return cg_ok


_BWRAP_LIVE = _bwrap_live()
_NEEDS_BWRAP = pytest.mark.skipif(
    not _BWRAP_LIVE, reason="real bwrap sandbox not viable on this host (live-run outcomes)"
)


# --- FAKED transport: the Telegram bot HTTP layer (in-process capture) ----------


class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):  # noqa: ANN001
        self.messages.append({"chat_id": chat_id, "text": text})

    async def answer_callback_query(self, callback_id, text=None):  # noqa: ANN001
        pass


class _FakeBotApp:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot

    def add_handler(self, handler: object) -> None:
        pass


# --- FAKED consent UX: records the summary it was shown, then approves/denies ----


class _RecordingPrompter:
    """Captures every ConsentRequest.summary, then returns a fixed scope.

    The journey asserts the recorded summary CONTAINED the actual code (GAP-A).
    """

    def __init__(self, scope: ConsentScope) -> None:
        self._scope = scope
        self.requests: list[ConsentRequest] = []

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        self.requests.append(req)
        return self._scope


# --- FAKED (THE ONLY AI MOCK): the secretary owl's scripted provider ------------


class _ScriptedSecretary:
    """Scripted secretary LLM: calls execute_code once, threads real stdout out."""

    protocol = "anthropic"
    name = "scripted-secretary"

    def __init__(self, *, code: str, network: bool = False) -> None:
        self._code = code
        self._network = network
        self.exec_out: str = ""
        self.final: str = ""

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, persistence_check=None, **kwargs,
    ):
        args = {"code": self._code, "network": self._network}
        self.exec_out = await tool_dispatcher("execute_code", args)
        # Thread whatever came back (real stdout on success, refusal text otherwise).
        try:
            record = json.loads(self.exec_out).get("record", {})
            stdout = record.get("stdout", "")
            self.final = f"The code ran. Output: {stdout.strip()}"
        except (json.JSONDecodeError, AttributeError):
            self.final = f"Could not run the code: {self.exec_out}"
        return (self.final, [{"name": "execute_code", "args": args, "result": self.exec_out}])

    async def complete(self, *a, **k) -> CompletionResult:  # noqa: ANN002,ANN003
        return CompletionResult(
            content="", input_tokens=1, output_tokens=1, model="scripted",
            provider_name="scripted-secretary", duration_ms=0.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover
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
    prompter: _RecordingPrompter
    delegation_depth: int = 0
    extra_state: dict = field(default_factory=dict)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _build(
    *,
    code: str,
    network: bool = False,
    scope: ConsentScope = ConsentScope.ONCE,
    wire_selector: bool = True,
    selector: SandboxSelector | None = None,
    delegation_depth: int = 0,
) -> _Env:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    provider = _ScriptedSecretary(code=code, network=network)

    # REAL consent gate wired to a per-channel recording prompter (telegram channel).
    prompter = _RecordingPrompter(scope)
    routing = RoutingPrompter()
    routing.register("telegram", prompter)
    consent_gate = ConsequentialActionGate(ConsentPolicy(prompter=routing))

    # REAL SandboxSelector with the REAL backends (bwrap-primary). Unless the test
    # asks for the unwired/custom case.
    if wire_selector and selector is None:
        selector = SandboxSelector(
            backends=[BwrapSandbox(enabled=True), DockerSandbox(enabled=True)]
        )
    if not wire_selector:
        selector = None

    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),  # REAL execute_code
        consent_gate=consent_gate,
        stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
        sandbox_selector=selector,  # REAL selector (drop this → unavailable)
    )
    return _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services),
        stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider, prompter=prompter, delegation_depth=delegation_depth,
    )


async def _drive(env: _Env, text: str) -> None:
    """Drive one user message through the REAL gateway path to a final reply.

    A delegated sub-pipeline (``delegation_depth>0``) does NOT write to the user's
    stream (the deliver step routes its result to the parent, not the user) — so for
    that case we run the backend WITHOUT awaiting a user-facing send (which would
    correctly never complete). The assertion is on the dispatch-layer refusal.
    """
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    msg = await env.adapter.receive()
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text  # type: ignore[attr-defined]
    _writer, reader = env.stream_registry.create(msg.trace_id)  # type: ignore[attr-defined]
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,  # type: ignore[attr-defined]
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",  # type: ignore[attr-defined]
        delegation_depth=env.delegation_depth,
    )
    if env.delegation_depth > 0:
        # Sub-pipeline: no user-facing stream write; just run + inspect dispatch.
        await asyncio.wait_for(env.backend.run(state), timeout=30.0)
        env.stream_registry.remove(msg.trace_id)  # type: ignore[attr-defined]
        return
    run_task = asyncio.create_task(env.backend.run(state))
    send_task = asyncio.create_task(env.adapter.send(reader))
    await asyncio.wait_for(run_task, timeout=30.0)
    await asyncio.wait_for(send_task, timeout=5.0)
    env.stream_registry.remove(msg.trace_id)  # type: ignore[attr-defined]


def _delivered(env: _Env) -> str:
    raw = "\n".join(m["text"] for m in env.bot.messages if m["chat_id"] == USER_ID)
    return raw.replace("\\", "")  # strip MarkdownV2 escaping


# =====================================================================
# OUTCOME 1 + 2 — real run: the user's reply contains the REAL output "4",
# produced by a REAL bwrap sandbox; provenance says bwrap/docker + no network.
# =====================================================================


@_NEEDS_BWRAP
async def test_j11_run_code_real_output_reaches_user() -> None:
    env = _build(code="print(2 + 2)", scope=ConsentScope.ONCE)
    await _drive(env, "What is 2 + 2? Run code to be sure.")

    record = json.loads(env.provider.exec_out)["record"]
    # OUTCOME 1 — the REAL output "4" reaches the user.
    assert record["stdout"].strip() == "4", record
    assert "4" in _delivered(env), (
        f"OUTCOME 1 FAIL: real output '4' did not reach the user. Sent: {env.bot.messages!r}"
    )
    # OUTCOME 2 — provenance: a REAL isolation backend ran it, with NO network.
    assert record["exit_reason"] == "ok", record
    assert record["exit_code"] == 0, record
    assert record["backend"] in {"bwrap", "docker"}, record
    assert record["network_enabled"] is False, record


# =====================================================================
# OUTCOME 3 — network is genuinely DENIED: code that opens a socket with
# network=False FAILS (real OSError inside the isolated net namespace).
# =====================================================================


@_NEEDS_BWRAP
async def test_j11_network_denied_socket_fails() -> None:
    code = (
        "import socket\n"
        "socket.create_connection(('1.1.1.1', 80), timeout=3)\n"
        "print('CONNECTED')\n"
    )
    env = _build(code=code, network=False, scope=ConsentScope.ONCE)
    await _drive(env, "Try to reach the internet.")

    record = json.loads(env.provider.exec_out)["record"]
    # The program ran (exit_reason ok) but the connection could NOT be made.
    assert record["network_enabled"] is False, record
    assert "CONNECTED" not in record["stdout"], (
        f"OUTCOME 3 FAIL: the sandbox reached the network with network=False. {record!r}"
    )
    # The socket attempt failed with a real network error inside the sandbox.
    assert record["exit_code"] != 0, record
    assert "CONNECTED" not in _delivered(env)


# =====================================================================
# OUTCOME 4 — a delegated child (delegation_depth>0) is REFUSED execute_code
# at the dispatch layer (GAP-B). Nothing runs; the consent prompt never fires.
# =====================================================================


async def test_j11_delegated_child_is_refused_execute_code() -> None:
    env = _build(code="print(2 + 2)", scope=ConsentScope.ONCE, delegation_depth=1)
    await _drive(env, "Run some code.")

    # The dispatch layer returns the child-refusal string; no record/run happened.
    assert "not available to a delegated sub-agent" in env.provider.exec_out, (
        f"OUTCOME 4 FAIL: a depth>0 child was NOT refused. exec_out={env.provider.exec_out!r}"
    )
    # The consent prompt was never even reached (refused before the gate).
    assert env.prompter.requests == [], (
        f"OUTCOME 4 FAIL: consent prompted for a refused child. {env.prompter.requests!r}"
    )


# =====================================================================
# OUTCOME 5 — selector UNAVAILABLE → structured "unavailable", ZERO host exec.
# This is the load-bearing safety: no sandbox ⇒ code NEVER runs on the host.
# =====================================================================


async def test_j11_unavailable_selector_runs_nothing_on_host() -> None:
    # No selector wired at all (the fail-when-unwired proof).
    env = _build(code="print('SHOULD_NOT_RUN')", wire_selector=False, scope=ConsentScope.ONCE)
    await _drive(env, "Run code.")

    assert "unavailable" in env.provider.exec_out, (
        f"OUTCOME 5 FAIL: no selector did NOT yield 'unavailable'. {env.provider.exec_out!r}"
    )
    # Proof nothing ran on the host: the sentinel the code would have printed is absent.
    assert "SHOULD_NOT_RUN" not in env.provider.exec_out
    assert "SHOULD_NOT_RUN" not in _delivered(env)


# =====================================================================
# OUTCOME 6 — consent prompt SHOWS the code (GAP-A) on the approve path.
# =====================================================================


@_NEEDS_BWRAP
async def test_j11_consent_prompt_shows_the_code() -> None:
    env = _build(code="print(2 + 2)", scope=ConsentScope.ONCE)
    await _drive(env, "Run code.")

    assert env.prompter.requests, "OUTCOME 6 FAIL: the consent gate never prompted."
    summary = env.prompter.requests[0].summary
    # The prompt showed the ACTUAL code + language + network posture — not the
    # generic tool description (GAP-A: the user consents to what really runs).
    assert "print(2 + 2)" in summary, (
        f"OUTCOME 6 FAIL: consent prompt did not show the code. summary={summary!r}"
    )
    assert "python" in summary and "no network" in summary, summary


# =====================================================================
# OUTCOME 7 — consent DENIED → code never runs (no output, structured refusal).
# =====================================================================


async def test_j11_consent_denied_runs_nothing() -> None:
    env = _build(code="print('SHOULD_NOT_RUN')", scope=ConsentScope.DENY)
    await _drive(env, "Run code.")

    assert env.prompter.requests, "consent gate should have prompted"
    # Denied → the dispatch layer returns the 'requires approval' refusal; no run.
    assert "approval" in env.provider.exec_out or "declined" in env.provider.exec_out, (
        f"OUTCOME 7 FAIL: a denied call was not refused. exec_out={env.provider.exec_out!r}"
    )
    assert "SHOULD_NOT_RUN" not in _delivered(env)

"""J12 JOURNEY — "Run code that calls back to my tools" (E11, S4 PTC).

The business requirement, in user terms:

  > The owl writes a snippet that, while running in the sandbox, calls back to a
  > CURATED set of my host tools (read a file, search the web, read/write a sandbox
  > file) — and the real result reaches me. But if the code tries to call a dangerous
  > tool (shell, run more code), it is refused — the host tool never runs.

THE headline outcome: code running in a REAL bwrap sandbox does ``import owl;
owl.read_file(...)``, the REAL host read_file runs, and its content reaches the user's
reply — driven end-to-end through the GENUINE gateway, mocking ONLY the AI provider +
bot transport + consent prompter (auto-approve). The safety outcome: a variant whose
code calls ``owl.shell(...)`` gets a clean refusal in-sandbox and the host shell tool
NEVER runs.

REAL (everything except AI + transport + consent UX): the whole pipeline, the REAL
``ToolRegistry`` + ``ExecuteCodeTool`` + ``PtcServer`` + the REAL ``BwrapSandbox``.

SKIP-with-reason when bwrap isn't live (the live-callback outcomes need a real sandbox).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import GatewayScanner
from stackowl.owls.registry import OwlRegistry
from stackowl.paths import StackowlHome
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
from stackowl.tools.consent import ConsentPolicy, ConsentScope, RoutingPrompter
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 828282


def _bwrap_live() -> bool:
    if shutil.which("bwrap") is None:
        return False
    probe = SandboxCapability.probe()
    if not probe.bwrap_viable:
        return False
    cg_ok, _ = CgroupRecipe.delegation_available()
    return cg_ok


_NEEDS_BWRAP = pytest.mark.skipif(
    not _bwrap_live(), reason="real bwrap sandbox not viable (PTC live-callback outcomes)"
)


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


class _AutoApprove:
    async def prompt(self, req):  # noqa: ANN001
        return ConsentScope.ONCE


class _ScriptedSecretary:
    """Scripted secretary: calls execute_code once, threads the real stdout out."""

    protocol = "anthropic"
    name = "scripted-secretary"

    def __init__(self, *, code: str) -> None:
        self._code = code
        self.exec_out: str = ""
        self.final: str = ""

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, persistence_check=None, **kwargs,
    ):
        args = {"code": self._code, "network": False}
        self.exec_out = await tool_dispatcher("execute_code", args)
        try:
            record = json.loads(self.exec_out).get("record", {})
            self.final = f"Done. Output: {record.get('stdout', '').strip()}"
        except (json.JSONDecodeError, AttributeError):
            self.final = f"Could not run: {self.exec_out}"
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


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _build(*, code: str) -> _Env:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    provider = _ScriptedSecretary(code=code)
    routing = RoutingPrompter()
    routing.register("telegram", _AutoApprove())
    consent_gate = ConsequentialActionGate(ConsentPolicy(prompter=routing))
    selector = SandboxSelector(backends=[BwrapSandbox(enabled=True), DockerSandbox(enabled=True)])

    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),  # REAL execute_code + read_file
        consent_gate=consent_gate,
        stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
        sandbox_selector=selector,
    )
    return _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services),
        stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider,
    )


async def _drive(env: _Env, text: str) -> None:
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    msg = await env.adapter.receive()
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text  # type: ignore[attr-defined]
    _writer, reader = env.stream_registry.create(msg.session_id)  # type: ignore[attr-defined]
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,  # type: ignore[attr-defined]
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",  # type: ignore[attr-defined]
    )
    run_task = asyncio.create_task(env.backend.run(state))
    send_task = asyncio.create_task(env.adapter.send(reader))
    await asyncio.wait_for(run_task, timeout=40.0)
    await asyncio.wait_for(send_task, timeout=5.0)
    env.stream_registry.remove(msg.session_id)  # type: ignore[attr-defined]


def _delivered(env: _Env) -> str:
    raw = "\n".join(m["text"] for m in env.bot.messages if m["chat_id"] == USER_ID)
    return raw.replace("\\", "")


# =====================================================================
# OUTCOME 1 — the ALLOWED host-tool callback works: code in a REAL sandbox
# does `import owl; owl.read_file(...)`, the REAL host read_file runs, and the
# content reaches the user.
# =====================================================================


@_NEEDS_BWRAP
async def test_j12_allowed_callback_content_reaches_user() -> None:
    # Seed a file in the host workspace that read_file (workspace-confined) can read.
    ws = StackowlHome.workspace()
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "report.txt").write_text("PTC-CALLBACK-CONTENT-OK")

    code = (
        "import owl\n"
        "print('FILE:' + owl.read_file('report.txt'))\n"
    )
    env = _build(code=code)
    await _drive(env, "Read report.txt from inside the sandbox and tell me.")

    record = json.loads(env.provider.exec_out)["record"]
    assert record["exit_reason"] == "ok", record
    assert "FILE:PTC-CALLBACK-CONTENT-OK" in record["stdout"], record["stdout"]
    # The real host-tool content reached the user's delivered reply.
    assert "PTC-CALLBACK-CONTENT-OK" in _delivered(env), (
        f"OUTCOME 1 FAIL: callback content did not reach the user. {env.bot.messages!r}"
    )


# =====================================================================
# OUTCOME 2 — a HARD-EXCLUDED callback (owl.shell) is REFUSED: the code gets a
# clean error in-sandbox and the host shell tool NEVER runs.
# =====================================================================


@_NEEDS_BWRAP
async def test_j12_excluded_shell_callback_is_refused() -> None:
    code = (
        "import owl\n"
        "try:\n"
        "    owl.shell(command='echo pwned')\n"
        "    print('SHELL_RAN')\n"
        "except Exception as e:\n"
        "    print('REFUSED:' + str(e))\n"
    )
    env = _build(code=code)
    await _drive(env, "Try to run a shell command from inside the sandbox.")

    record = json.loads(env.provider.exec_out)["record"]
    assert record["exit_reason"] == "ok", record
    # The sandbox could NOT reach shell; it got a clean refusal instead.
    assert "SHELL_RAN" not in record["stdout"], "shell was reachable — exclusion breached"
    assert "REFUSED:" in record["stdout"], record["stdout"]
    assert "not callable from a sandbox" in record["stdout"], record["stdout"]
    assert "pwned" not in _delivered(env)

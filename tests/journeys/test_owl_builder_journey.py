"""OWL-BUILDER GATEWAY JOURNEY — build a specialist, route a turn, enforce bounds.

End-to-end proof of the owl-builder business value with REAL wiring (the only
mock is the AI provider):

  1. BUILD — a human runs the REAL ``/owls add rsr --role research --tier fast
     --preset researcher`` command (``OwlsCommand._add``). A real ``researcher``
     manifest with safe-by-construction bounds (tools EXCLUDE ``shell``, INCLUDE
     ``web_fetch`` + the boundary-router ``delegate_task``) lands in the registry
     AND is persisted to ``stackowl.yaml``.
  2. ROUTE — a turn is routed to ``rsr`` (``@rsr``). The scripted provider calls,
     in order, an IN-BOUNDS tool (``web_fetch``) then an OUT-OF-BOUNDS tool
     (``shell``) through the REAL ingress → gateway → pipeline → execute seam.
  3. ENFORCE — outcomes asserted at the REAL ``execute._run_with_tools._dispatch``
     enforcement seam (the same seam the S2 task-scope journey exercises):
       * ``web_fetch`` (in-bounds) was DISPATCHED — its real execute() ran.
       * ``shell`` (out-of-bounds) was BLOCKED by the owl's OWN bounds — execute
         never ran, the model got a clean "not permitted" reason, no crash.
       * ``delegate_task`` (the boundary-router) is present in the owl's effective
         bounds — a narrow owl is additive, not a dead-end.
       * the turn still delivered a final reply (a bounds block is a clean path).
  4. PERSIST — a fresh ``Settings()`` + ``OwlRegistry.from_settings`` reload reads
     the SAME ``stackowl.yaml`` back from disk. The reloaded ``rsr`` STILL has
     ``bounds`` with ``shell`` excluded and ``delegate_task`` included.

Scaffolding (Telegram doubles, _build, _turn) is REUSED from the sibling S2/S3
journeys (``test_tool_scope_envelope.py`` / ``test_preflight_envelope.py``). The
ONLY mock is the scripted AI provider; every other piece is real production
wiring (builder, command, bounds, persistence, the Epic-2 enforcement seam).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.commands.owls_command import OwlsCommand
from stackowl.config.settings import Settings
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import GatewayScanner
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult, Message
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_ID = 717171

_OWL = "rsr"
_IN_BOUNDS_TOOL = "web_fetch"  # in the researcher preset
_OUT_OF_BOUNDS_TOOL = "shell"  # NOT in the researcher preset
_ROUTER_TOOL = "delegate_task"  # the boundary-router (ROUTER_TOOLS)
_IN_BOUNDS_OUTPUT = "FETCH-RESULT: page fetched"
_FINAL_REPLY = "I fetched that for you; I'm not permitted to run shell, so I stopped there."
# Punctuation-free fragment — the Telegram adapter MarkdownV2-escapes outbound.
_REPLY_FRAGMENT = "not permitted to run shell"


# ---------------------------------------------------------------------------
# FAKED #1: Telegram bot HTTP transport (captures outbound in-process)
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
# REAL tools: read-severity, record whether execute() actually ran
# ---------------------------------------------------------------------------


class _RecordingTool(Tool):
    def __init__(self, name: str, output: str) -> None:
        self._name = name
        self._output = output
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
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.runs += 1
        return ToolResult(success=True, output=self._output, error=None, duration_ms=1.0)


# ---------------------------------------------------------------------------
# FAKED #2 (THE ONLY AI MOCK): the researcher owl's scripted provider
# ---------------------------------------------------------------------------


class _ScriptedResearcher:
    """The ONLY mock.  Drives the REAL tool loop via the REAL tool_dispatcher —
    calls the in-bounds tool then the out-of-bounds ``shell`` tool so the owl's
    own bounds enforcement is exercised, then returns the canonical final reply.
    """

    protocol = "anthropic"

    def __init__(self) -> None:
        self.in_bounds_out: str = ""
        self.out_of_bounds_out: str = ""

    @property
    def name(self) -> str:
        return _OWL

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None, **_kw
    ):
        self.in_bounds_out = await tool_dispatcher(_IN_BOUNDS_TOOL, {})
        self.out_of_bounds_out = await tool_dispatcher(_OUT_OF_BOUNDS_TOOL, {})
        return (_FINAL_REPLY, [])

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        return CompletionResult(
            content="I'll research that and stay within my permitted tools.",
            input_tokens=6, output_tokens=8, model="rsr-model",
            provider_name=_OWL, duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover — not on this path
        if False:
            yield ""


class _FakeProviderRegistry:
    def __init__(self, p: _ScriptedResearcher) -> None:
        self._p = p

    def get(self, name: str) -> _ScriptedResearcher:
        return self._p

    def get_by_tier(self, tier: str) -> _ScriptedResearcher:
        return self._p

    def get_with_cascade(self, preferred_tier: str) -> _ScriptedResearcher:
        return self._p


# ---------------------------------------------------------------------------
# Env wiring (modeled on the established S2/S3 journey harness)
# ---------------------------------------------------------------------------


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: _ScriptedResearcher
    owl_registry: OwlRegistry
    tool_registry: ToolRegistry
    in_bounds: _RecordingTool
    out_of_bounds: _RecordingTool


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


@pytest.fixture()
def _config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point both the command's yaml writer AND the Settings yaml source at a
    temp file via STACKOWL_CONFIG_FILE (StackowlHome.config_file reads it)."""
    cfg = tmp_path / "stackowl.yaml"
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
    return cfg


def _build(provider: _ScriptedResearcher) -> _Env:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)  # type: ignore[assignment]
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    in_bounds = _RecordingTool(_IN_BOUNDS_TOOL, _IN_BOUNDS_OUTPUT)
    out_of_bounds = _RecordingTool(_OUT_OF_BOUNDS_TOOL, "SHOULD-NEVER-APPEAR")
    tool_registry = ToolRegistry()
    tool_registry.register(in_bounds)
    tool_registry.register(out_of_bounds)
    # Register the rest of the researcher preset + router tools so the builder's
    # catalog validation keeps the FULL researcher allowlist (otherwise unknown
    # tools are dropped and the persisted bounds would be a narrowed subset).
    for extra_name in ("read_file", "memory", "web_search", "delegate_task",
                       "tool_search", "tool_describe"):
        tool_registry.register(_RecordingTool(extra_name, f"OK:{extra_name}"))

    owl_registry = OwlRegistry.with_default_secretary()

    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=tool_registry,
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        owl_registry=owl_registry,
    )
    return _Env(
        adapter=adapter, bot=bot,
        scanner=GatewayScanner(owl_registry=owl_registry),
        backend=AsyncioBackend(services=services),  # type: ignore[arg-type]
        stream_registry=services.stream_registry, provider=provider,
        owl_registry=owl_registry, tool_registry=tool_registry,
        in_bounds=in_bounds, out_of_bounds=out_of_bounds,
    )


async def _turn(env: _Env, text: str) -> str:
    """Drive one inbound turn through the full gateway arc (ingress → scanner →
    pipeline → execute → outbound)."""
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
    await run_task
    await out_task
    env.stream_registry.remove(msg.trace_id)
    return "".join(m["text"] for m in env.bot.messages[before:] if m["reply_markup"] is None)


# ===========================================================================
# JOURNEY — build the researcher, route a turn, enforce bounds, persist
# ===========================================================================


async def test_owl_builder_journey_build_route_enforce_persist(
    _config_file: Path,
) -> None:
    """The full owl-builder value: a human builds a researcher specialist whose
    bounds are enforced end-to-end, the boundary-router stays available, and the
    specialist + its bounds survive a config reload.

    The ONLY mock is the scripted AI provider — the builder, the command, the
    bounds, the persistence, and the Epic-2 enforcement seam are all real.
    """
    provider = _ScriptedResearcher()
    env = _build(provider)

    # --- 1. BUILD via the REAL /owls add command (no ceiling — the owl's OWN
    #        researcher-preset bounds must do the blocking) -------------------
    cmd = OwlsCommand(
        owl_registry=env.owl_registry,
        tool_registry=env.tool_registry,
    )
    reply = await cmd.handle(
        f"add {_OWL} --role research --tier fast --preset researcher",
        PipelineState(
            trace_id="build", session_id="build", input_text="", channel="cli",
            owl_name="secretary", pipeline_step="start",
        ),
    )
    assert reply.startswith("✓"), f"build command failed: {reply!r}"

    # The researcher manifest with safe-by-construction bounds is in the registry.
    manifest = env.owl_registry.get(_OWL)
    assert manifest.bounds is not None, "built researcher owl has no bounds"
    assert _OUT_OF_BOUNDS_TOOL not in manifest.bounds.tools, (
        f"researcher preset must EXCLUDE '{_OUT_OF_BOUNDS_TOOL}'; "
        f"got tools={sorted(manifest.bounds.tools)}"
    )
    assert _IN_BOUNDS_TOOL in manifest.bounds.tools, (
        f"researcher preset must INCLUDE '{_IN_BOUNDS_TOOL}'; "
        f"got tools={sorted(manifest.bounds.tools)}"
    )
    # The boundary-router is present so a narrow owl is additive, not a dead-end.
    assert _ROUTER_TOOL in manifest.bounds.tools, (
        f"the boundary-router '{_ROUTER_TOOL}' must be in the built owl's bounds; "
        f"got tools={sorted(manifest.bounds.tools)}"
    )

    # --- 2 + 3. ROUTE a turn to rsr and ENFORCE bounds at the real seam ------
    reply = await _turn(env, f"@{_OWL} fetch the page and then run a shell command")

    # OUTCOME 1 — the in-bounds tool genuinely RAN (owl bounds permit it).
    assert env.in_bounds.runs == 1, "the in-bounds tool did not run for the researcher owl"
    assert provider.in_bounds_out == _IN_BOUNDS_OUTPUT

    # OUTCOME 2 — the out-of-bounds 'shell' tool was BLOCKED by the owl's OWN
    # bounds at the REAL execute._run_with_tools._dispatch seam: execute never
    # ran, no crash, the model received a clean "not permitted" reason.
    assert env.out_of_bounds.runs == 0, (
        "BOUNDS BREACH: the out-of-bounds 'shell' tool's execute ran even though "
        "the researcher owl's bounds exclude it"
    )
    assert "not permitted by this owl's bounds" in provider.out_of_bounds_out, (
        f"Expected bounds-block reason in out_of_bounds_out, got: {provider.out_of_bounds_out!r}"
    )
    assert "SHOULD-NEVER-APPEAR" not in provider.out_of_bounds_out

    # OUTCOME 3 — the session CONTINUED and DELIVERED a final reply (a bounds
    # block is a clean path, not a dead-end/crash).
    assert _REPLY_FRAGMENT in reply, (
        f"The turn did not deliver a final reply under the owl's bounds. Got: {reply!r}"
    )

    # --- 4. PERSIST — reload the SAME yaml from disk and re-check the bounds --
    assert _config_file.exists(), "the build command did not persist the owl to yaml"
    reloaded_registry = OwlRegistry.from_settings(Settings())
    reloaded = reloaded_registry.get(_OWL)
    assert reloaded.bounds is not None, "reloaded researcher owl lost its bounds"
    assert _OUT_OF_BOUNDS_TOOL not in reloaded.bounds.tools, (
        f"after reload, '{_OUT_OF_BOUNDS_TOOL}' leaked into the researcher owl's bounds; "
        f"got tools={sorted(reloaded.bounds.tools)}"
    )
    assert _ROUTER_TOOL in reloaded.bounds.tools, (
        f"after reload, the boundary-router '{_ROUTER_TOOL}' was lost; "
        f"got tools={sorted(reloaded.bounds.tools)}"
    )


# ===========================================================================
# CONTROL — proves it is the OWL BOUNDS (not a missing tool) that blocks shell
# ===========================================================================


async def test_unbounded_owl_runs_shell_proving_bounds_is_the_blocker(
    _config_file: Path,
) -> None:
    """CONTROL for the journey: an UNBOUNDED owl (built with NO preset/tools →
    ``bounds is None``) routed the same way runs BOTH tools — proving that in the
    companion journey it was the researcher BOUNDS (not a missing/unregistered
    tool) that blocked ``shell``. Without this control, an accidentally absent
    ``shell`` registration would make the deny test pass vacuously.
    """
    provider = _ScriptedResearcher()
    env = _build(provider)

    # Build a bare owl (no preset, no tools) → unbounded (bounds is None).
    cmd = OwlsCommand(owl_registry=env.owl_registry, tool_registry=env.tool_registry)
    reply = await cmd.handle(
        f"add {_OWL} --role research --tier fast",
        PipelineState(
            trace_id="build", session_id="build", input_text="", channel="cli",
            owl_name="secretary", pipeline_step="start",
        ),
    )
    assert reply.startswith("✓"), f"build command failed: {reply!r}"
    assert env.owl_registry.get(_OWL).bounds is None, "bare owl should be unbounded"

    _ = await _turn(env, f"@{_OWL} fetch the page and then run a shell command")

    # Both tools ran — nothing narrows the unbounded owl.
    assert env.in_bounds.runs == 1, "the in-bounds tool did not run (unbounded owl)"
    assert env.out_of_bounds.runs == 1, (
        "CONTROL FAILURE: 'shell' did not run under an UNBOUNDED owl. Something "
        "other than the researcher bounds is blocking it, which would make "
        "test_owl_builder_journey_build_route_enforce_persist vacuous."
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])

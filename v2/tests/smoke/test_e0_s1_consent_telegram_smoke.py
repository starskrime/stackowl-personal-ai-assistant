"""E0-S1 SMOKE — consent gate driven AS THE USER, Telegram input → end.

This is NOT a direct execute() call. A fake inbound Telegram update traverses
the GENUINE path: TelegramChannelAdapter._handle_update → GatewayScanner →
AsyncioBackend (full 8-step pipeline) → execute._dispatch → ConsequentialActionGate
→ ConsentPolicy → TelegramConsentPrompter → adapter.send_inline_keyboard. The
user "taps" a button, routed through the REAL CallbackRouter, which resolves the
prompt; the tool is then blocked or run, and the response is delivered back out
through adapter.send. Audit rows are asserted along the trace.

A fake bot transport captures outbound calls — no real network — but every
StackOwl component on the path (adapter, scanner, backend, pipeline, gate,
policy, prompter, callback router, stream) is the real one.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from stackowl.audit.logger import AuditLogger
from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.callbacks import CallbackRouter
from stackowl.channels.telegram.consent import TelegramConsentPrompter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.consent import ConsentPolicy, RoutingPrompter
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 424242


# --------------------------------------------------------------------------- #
# Fake Telegram transport (captures outbound; no network)
# --------------------------------------------------------------------------- #
class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []  # {chat_id, text, reply_markup}
        self.answered: list[str] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):  # noqa: ANN001
        self.messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    async def answer_callback_query(self, callback_id, text=None):  # noqa: ANN001
        self.answered.append(callback_id)


class _FakeBotApp:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot
        self.handlers: list[object] = []

    def add_handler(self, handler: object) -> None:
        self.handlers.append(handler)


# --------------------------------------------------------------------------- #
# A consequential tool that records whether it actually ran
# --------------------------------------------------------------------------- #
class _DangerTool(Tool):
    def __init__(self, name: str = "danger", category: str | None = None) -> None:
        self._name = name
        self._category = category
        self.executed = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "Do the dangerous thing"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self.description, parameters=self.parameters,
            action_severity="consequential", consent_category=self._category,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.executed = True
        # alphanumeric so the Telegram MarkdownV2 formatter doesn't escape it
        return ToolResult(success=True, output="DANGERDONE", duration_ms=1.0)


class _FakeProvider:
    """Tool-loop provider: dispatches the named tool once, returns its result text."""

    protocol = "anthropic"

    def __init__(self, tool_name: str) -> None:
        self._tool_name = tool_name

    async def complete_with_tools(
        self, *, user_text, system_text, tool_schemas,
        tool_dispatcher, history=None, **_kwargs,
    ):  # noqa: ANN001
        result = await tool_dispatcher(self._tool_name, {})
        return (result, [{"name": self._tool_name, "args": {}, "result": result}])

    async def complete(self, *a, **k):  # pragma: no cover - defensive for other steps
        return ""

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


class _FakeProviderRegistry:
    def __init__(self, provider: _FakeProvider) -> None:
        self._p = provider

    def get(self, name: str) -> _FakeProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _FakeProvider:
        return self._p


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    callback_router: CallbackRouter
    audit: AuditLogger
    tool: _DangerTool


async def _build_env(db: DbPool, audit_path: Path, tool: _DangerTool) -> _Env:
    bot = _FakeBot()
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    adapter._bot_app = _FakeBotApp(bot)  # inject fake transport (skip real start_bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    # AuditLogger uses its OWN sqlite file (separate from the open DbPool, which
    # would otherwise lock under concurrent writes). Create its audit_log table.
    conn = sqlite3.connect(audit_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit_log ("
        "audit_id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, "
        "actor TEXT, target TEXT, timestamp REAL NOT NULL, details TEXT NOT NULL, "
        "integrity_hash TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()
    audit = AuditLogger(audit_path)
    routing = RoutingPrompter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=5.0)
    routing.register("telegram", prompter)
    gate = ConsequentialActionGate(ConsentPolicy(prompter=routing, audit_logger=audit))

    router = CallbackRouter(db, adapter)
    await router.ensure_table()
    router.register("consent:", prompter.handle_callback)
    adapter.attach_callback_router(router)

    tools = ToolRegistry()
    tools.register(tool)

    services = StepServices(
        provider_registry=_FakeProviderRegistry(_FakeProvider(tool.name)),  # type: ignore[arg-type]
        tool_registry=tools,
        consent_gate=gate,
        stream_registry=StreamRegistry(),
        db_pool=db,
    )
    return _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        callback_router=router, audit=audit, tool=tool,
    )


def _cd_for(markup, scope: str) -> str:  # noqa: ANN001
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data.endswith(f":{scope}"):
                return btn.callback_data
    raise AssertionError(f"no {scope} button in consent keyboard")


async def _tap(env: _Env, scope: str, *, since: int = 0) -> bool:
    """Wait for a NEW consent keyboard (beyond ``since`` prior keyboards) and tap it.

    ``since`` avoids tapping a stale, already-resolved keyboard from a prior turn.
    """
    for _ in range(250):  # up to ~5s
        kb = [m for m in env.bot.messages if m["reply_markup"] is not None]
        if len(kb) > since:
            cd = _cd_for(kb[-1]["reply_markup"], scope)
            update = SimpleNamespace(
                callback_query=SimpleNamespace(id=f"cb-{len(env.bot.answered)}", data=cd)
            )
            await env.callback_router.route(update, None)
            return True
        await asyncio.sleep(0.02)
    raise AssertionError("consent prompt never appeared on Telegram")


async def _turn(env: _Env, text: str, *, tap: str | None) -> str:
    """One full inbound→outbound turn. Returns the delivered outbound text."""
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)  # real intake (auth + enqueue)
    msg = await env.adapter.receive()
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    _writer, reader = env.stream_registry.create(msg.trace_id)
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
    )
    before = len(env.bot.messages)
    kb_before = len([m for m in env.bot.messages if m["reply_markup"] is not None])
    run_task = asyncio.create_task(env.backend.run(state))
    # Real outbound delivery: adapter.send drains the stream and send_text()s to Telegram.
    out_task = asyncio.create_task(env.adapter.send(reader))
    if tap is not None:
        await _tap(env, tap, since=kb_before)
    await run_task
    await out_task
    env.stream_registry.remove(msg.trace_id)
    # Outbound text = the non-keyboard messages the bot sent during this turn.
    new_msgs = env.bot.messages[before:]
    return "".join(m["text"] for m in new_msgs if m["reply_markup"] is None)


@pytest.fixture(autouse=True)
def _live_io(tmp_path: Path):  # noqa: ANN202
    # The smoke uses a fake bot (no real network); enable live-I/O paths.
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _audit_decisions(audit: AuditLogger) -> list[dict]:
    return [r for r in audit.tail(50) if r["event_type"] == "consent.decision"]


async def test_smoke_user_denies_blocks_tool_and_audits(tmp_db: DbPool, tmp_path: Path) -> None:
    tool = _DangerTool()
    env = await _build_env(tmp_db, tmp_path / "audit.db", tool)
    # Deny button now carries deny_session scope
    out = await _turn(env, "run the dangerous thing", tap="deny_session")

    assert tool.executed is False, "tool must NOT run after a deny"
    assert "DANGERDONE" not in out
    assert "approval" in out.lower() or "declined" in out.lower()
    # consent keyboard actually reached Telegram, targeting the user's chat
    kb = [m for m in env.bot.messages if m["reply_markup"] is not None]
    assert kb and kb[0]["chat_id"] == USER_ID
    decisions = _audit_decisions(env.audit)
    assert any(d["target"] == "danger" for d in decisions)
    import json
    assert any(json.loads(d["details"])["decision"] == "deny" for d in decisions)


async def test_smoke_user_approves_runs_tool_and_delivers(tmp_db: DbPool, tmp_path: Path) -> None:
    tool = _DangerTool()
    env = await _build_env(tmp_db, tmp_path / "audit.db", tool)
    # Approve button for non-excluded tools now carries session scope
    out = await _turn(env, "run the dangerous thing", tap="session")

    assert tool.executed is True, "tool must run after an approve"
    assert "DANGERDONE" in out
    import json
    decisions = _audit_decisions(env.audit)
    assert any(json.loads(d["details"])["decision"] == "allow" for d in decisions)


async def test_smoke_session_batch_suppresses_second_prompt(tmp_db: DbPool, tmp_path: Path) -> None:
    tool = _DangerTool()
    env = await _build_env(tmp_db, tmp_path / "audit.db", tool)

    # Turn 1: approve for the whole session.
    out1 = await _turn(env, "run the dangerous thing", tap="session")
    assert tool.executed is True
    assert "DANGERDONE" in out1
    prompts_after_turn1 = len([m for m in env.bot.messages if m["reply_markup"] is not None])

    # Turn 2: same tool, same session → NO new consent keyboard, tool runs.
    tool.executed = False
    out2 = await _turn(env, "do it again", tap=None)
    assert tool.executed is True, "session batch should auto-allow the second call"
    assert "DANGERDONE" in out2
    prompts_after_turn2 = len([m for m in env.bot.messages if m["reply_markup"] is not None])
    assert prompts_after_turn2 == prompts_after_turn1, "must NOT re-prompt within the granted session"


async def test_smoke_excluded_tool_reprompts_despite_session(tmp_db: DbPool, tmp_path: Path) -> None:
    # A lock-category tool must always re-prompt, even after a session grant.
    tool = _DangerTool(name="ha_lock", category="lock")
    env = await _build_env(tmp_db, tmp_path / "audit.db", tool)

    # Excluded tools don't even OFFER a session/window button — only once/deny.
    out1 = await _turn(env, "lock the door", tap="once")
    assert tool.executed is True and "DANGERDONE" in out1
    prompts1 = len([m for m in env.bot.messages if m["reply_markup"] is not None])
    # verify no relaxation button was offered for the excluded tool
    last_kb = [m for m in env.bot.messages if m["reply_markup"] is not None][-1]["reply_markup"]
    cds = [b.callback_data for row in last_kb.inline_keyboard for b in row]
    assert not any(c.endswith(":session") or c.endswith(":window") for c in cds)

    tool.executed = False
    out2 = await _turn(env, "lock it again", tap="once")  # MUST prompt again
    assert tool.executed is True and "DANGERDONE" in out2
    prompts2 = len([m for m in env.bot.messages if m["reply_markup"] is not None])
    assert prompts2 == prompts1 + 1, "excluded (lock) tool must re-prompt despite session grant"

"""E7-S1 SMOKE — cronjob create→list driven AS THE USER, Telegram input → end.

A real inbound Telegram update traverses the GENUINE path (adapter → scanner →
AsyncioBackend pipeline → execute._dispatch → ToolRegistry → CronjobTool → real
JobScheduler → real migrated SQLite ``jobs`` table). Turn 1 schedules a recurring
goal; turn 2 lists it back. Proves the tool is reachable by a real message, that
it PERSISTS a ``goal_execution`` job (``created_by='cronjob'``) on disk, and that
a FRESH scheduler reloads it across a simulated restart.

REAL: the DbPool (tmp_db, fully migrated), the pipeline, the tool, the scheduler.
FAKED (per the E4-S1 template): the provider (scripted tool calls instead of an
LLM) and the Telegram bot transport (captures outbound text in-process).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 858585


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


class _ScriptedProvider:
    protocol = "anthropic"

    def __init__(self) -> None:
        self.script: list[tuple[str, dict]] = []
        self.results: list[str] = []

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None):  # noqa: ANN001
        name, args = self.script.pop(0)
        out = await tool_dispatcher(name, args)
        self.results.append(out)
        return (out, [{"name": name, "args": args, "result": out}])

    async def complete(self, *a, **k):  # pragma: no cover
        return ""

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


class _FakeProviderRegistry:
    def __init__(self, p: _ScriptedProvider) -> None:
        self._p = p

    def get(self, name: str) -> _ScriptedProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _ScriptedProvider:
        return self._p


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: _ScriptedProvider


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


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
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.session_id)
    return ""


async def test_smoke_cronjob_create_then_list_through_telegram(tmp_db: DbPool) -> None:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""
    provider = _ScriptedProvider()
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        db_pool=tmp_db,  # REAL, fully-migrated DbPool — scheduler writes the jobs table
    )
    env = _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider,
    )

    # Turn 1: the user schedules a recurring goal (create path → persists a job).
    provider.script.append((
        "cronjob",
        {"action": "create", "prompt": "summarise my unread email", "schedule": "every 30m"},
    ))
    await _turn(env, "schedule a summary of my unread email every 30 minutes")

    created = json.loads(provider.results[0])
    assert created.get("created") is True, provider.results[0]
    # human recurrence surfaced ("every 30m" → 1440/30 = ~48x/day)
    assert "48x/day" in created.get("recurrence", ""), created
    created_job_id = created["job_id"]

    # The job is now durably on disk in the REAL tmp_db with the right shape.
    rows = await tmp_db.fetch_all("SELECT job_id, handler_name, params FROM jobs")
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["handler_name"] == "goal_execution", row
    params = json.loads(row["params"])
    assert params["goal"] == "summarise my unread email", params
    assert params["created_by"] == "cronjob", params

    # The outbound confirmation was delivered to the user over Telegram.
    assert bot.messages and bot.messages[-1]["chat_id"] == USER_ID

    # Turn 2: the user asks what is scheduled (list path → finds the created job).
    provider.script.append(("cronjob", {"action": "list"}))
    await _turn(env, "what have you scheduled")

    listed = json.loads(provider.results[1])
    assert listed.get("count") == 1, provider.results[1]
    assert listed["jobs"][0]["job_id"] == created_job_id, listed
    assert listed["jobs"][0]["goal"] == "summarise my unread email", listed
    assert bot.messages[-1]["chat_id"] == USER_ID

    # Persistence / reload proof (the story's smoke AC): a FRESH scheduler — as if
    # the process restarted — still sees the job, because it lives on disk in tmp_db.
    fresh = JobScheduler(db=tmp_db)
    reloaded = await fresh.list_jobs()
    assert any(j.job_id == created_job_id for j in reloaded), [j.job_id for j in reloaded]
    reloaded_job = next(j for j in reloaded if j.job_id == created_job_id)
    assert reloaded_job.handler_name == "goal_execution"
    assert reloaded_job.params["goal"] == "summarise my unread email"

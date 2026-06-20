"""Journey test: task_status tool is offered and resolves correctly.

Two prongs:
  (a) OFFERED — "task_status" appears in the tool schemas presented to the model
      by the real pipeline wired via ToolRegistry.with_defaults().
  (b) RESOLUTION — a scripted tool_dispatcher call to task_status(task_id="t1")
      returns the seeded task's real status from the DB store (not hallucination).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import GatewayScanner
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

if TYPE_CHECKING:
    from stackowl.db.pool import DbPool

USER_ID = 191919


# ---------------------------------------------------------------------------
# Fake infrastructure helpers
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


def _schema_name(schema: dict[str, object]) -> str:
    name = schema.get("name")
    if isinstance(name, str):
        return name
    fn = schema.get("function")
    if isinstance(fn, dict) and isinstance(fn.get("name"), str):
        return fn["name"]  # type: ignore[return-value]
    return ""


class _CapturingProvider(ModelProvider):
    """Captures tool schemas presented by the pipeline; optionally dispatches task_status."""

    protocol = "anthropic"

    def __init__(self, *, call_task_id: str | None = None) -> None:
        self.presented_tool_names: list[str] = []
        self.task_result: str = ""
        self._call_task_id = call_task_id
        self._called = False

    @property
    def name(self) -> str:
        return "secretary"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        return CompletionResult(
            content="secretary\nstandard", input_tokens=1, output_tokens=1,
            model="fake", provider_name="secretary", duration_ms=1.0,
        )

    async def stream(self, messages: list[Message], model: str, **kwargs: object):  # type: ignore[override]
        yield "done"

    async def complete_with_tools(self, *, tool_schemas=None, tool_dispatcher=None, **_kw: object) -> tuple[str, list[object]]:
        self.presented_tool_names = [_schema_name(s) for s in (tool_schemas or [])]
        if self._call_task_id and not self._called and "task_status" in self.presented_tool_names:
            self._called = True
            self.task_result = await tool_dispatcher("task_status", {"task_id": self._call_task_id})
        return ("task check complete", [])


class _FakeProviderRegistry:
    def __init__(self, p: _CapturingProvider) -> None:
        self._p = p

    def get(self, name: str) -> _CapturingProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _CapturingProvider:
        return self._p

    def get_with_cascade(self, preferred_tier: str) -> _CapturingProvider:
        return self._p


# ---------------------------------------------------------------------------
# Fixtures / harness
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _build_env(provider: _CapturingProvider, *, db: DbPool) -> tuple[TelegramChannelAdapter, _FakeBot, AsyncioBackend, StreamRegistry, OwlRegistry]:
    owl_registry = OwlRegistry.with_default_secretary()
    tool_registry = ToolRegistry.with_defaults()
    stream_registry = StreamRegistry()
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=tool_registry,
        consent_gate=ConsequentialActionGate(),
        stream_registry=stream_registry,
        owl_registry=owl_registry,
        db_pool=db,
    )
    token = set_services(services)

    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)  # type: ignore[assignment]
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    backend = AsyncioBackend(services=services)  # type: ignore[arg-type]
    return adapter, bot, backend, stream_registry, owl_registry, token  # type: ignore[return-value]


async def _run(text: str, *, provider: _CapturingProvider, db: DbPool) -> str:
    adapter, bot, backend, stream_registry, owl_registry, svc_token = _build_env(provider, db=db)
    try:
        update = SimpleNamespace(
            effective_message=SimpleNamespace(text=text),
            effective_user=SimpleNamespace(id=USER_ID),
            effective_chat=SimpleNamespace(id=USER_ID),
        )
        await adapter._handle_update(update, None)
        msg = await adapter.receive()
        decision = GatewayScanner(owl_registry=owl_registry).scan(msg)
        input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
        _writer, reader = stream_registry.create(msg.trace_id)
        state = PipelineState(
            trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
            channel=msg.channel, owl_name=decision.target, pipeline_step="start",
        )
        before = len(bot.messages)
        run_task = asyncio.create_task(backend.run(state))
        out_task = asyncio.create_task(adapter.send(reader))
        await run_task
        await out_task
        stream_registry.remove(msg.trace_id)
        return "".join(m["text"] for m in bot.messages[before:] if m["reply_markup"] is None)
    finally:
        reset_services(svc_token)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_task_status_offered_in_tool_schemas(tmp_db: DbPool) -> None:
    """task_status must appear in the tool schemas presented to the model."""
    provider = _CapturingProvider()
    await _run("what is the status of task t1?", provider=provider, db=tmp_db)
    assert "task_status" in provider.presented_tool_names, (
        f"task_status was NOT presented; presented={sorted(provider.presented_tool_names)}"
    )


async def test_task_status_resolves_seeded_task(tmp_db: DbPool) -> None:
    """A scripted task_status call resolves the real seeded status from the store."""
    now = datetime.now(tz=UTC)
    task = DurableTask(
        task_id="t1", owner_id=DEFAULT_PRINCIPAL_ID,
        goal="build the bridge", status="running", current_step=3,
        created_at=now, updated_at=now,
    )
    await DurableTaskStore(tmp_db, DEFAULT_PRINCIPAL_ID).create(task)

    provider = _CapturingProvider(call_task_id="t1")
    await _run("what is the status of task t1?", provider=provider, db=tmp_db)

    assert "task_status" in provider.presented_tool_names, (
        f"task_status not offered; presented={sorted(provider.presented_tool_names)}"
    )
    assert "running" in provider.task_result, (
        f"task_status did not return seeded status; got: {provider.task_result!r}"
    )
    assert "t1" in provider.task_result

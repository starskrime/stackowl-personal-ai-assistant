"""E4-S2 SMOKE — skill_manage consent round-trip, Telegram → real store → end.

The canonical consequential-tool smoke for the epic's most dangerous tool: a real
inbound Telegram message drives the GENUINE path (adapter → scanner →
AsyncioBackend → execute._dispatch → ConsequentialActionGate → ConsentPolicy →
TelegramConsentPrompter → inline keyboard); the user "taps" Approve through the
REAL CallbackRouter, and only THEN does skill_manage create a skill in a REAL
SkillIndexStore + tmp skills tree (validated + security-scanned + audited via the
provenance chokepoint). Without approval the self-edit must NOT happen.

reindex_after_change is stubbed (no embedder); everything else is real.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

import stackowl.tools.knowledge.skill_manage as sm
from stackowl.audit.logger import AuditLogger
from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.callbacks import CallbackRouter
from stackowl.channels.telegram.consent import TelegramConsentPrompter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner
from stackowl.paths import StackowlHome
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.consent import ConsentPolicy, RoutingPrompter
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 868686

_SKILL_MD = (
    "---\nname: greet-politely\ndescription: a courteous greeting procedure\n---\n\n"
    "## Steps\n\n1. Say hello warmly.\n2. Ask how you can help.\n"
)


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


class _SkillProvider:
    protocol = "anthropic"

    def __init__(self) -> None:
        self.result = ""

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None):  # noqa: ANN001
        self.result = await tool_dispatcher(
            "skill_manage", {"action": "create", "name": "greet-politely", "content": _SKILL_MD}
        )
        return (self.result, [{"name": "skill_manage", "args": {}, "result": self.result}])

    async def complete(self, *a, **k):  # pragma: no cover
        return ""

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


class _FakeProviderRegistry:
    def __init__(self, p: _SkillProvider) -> None:
        self._p = p

    def get(self, name: str) -> _SkillProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _SkillProvider:
        return self._p


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    callback_router: CallbackRouter
    provider: _SkillProvider


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _cd_for(markup, scope: str) -> str:  # noqa: ANN001
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data.endswith(f":{scope}"):
                return btn.callback_data
    raise AssertionError(f"no {scope} button in consent keyboard")


async def _tap(env: _Env, scope: str) -> None:
    for _ in range(250):
        kb = [m for m in env.bot.messages if m["reply_markup"] is not None]
        if kb:
            cd = _cd_for(kb[-1]["reply_markup"], scope)
            update = SimpleNamespace(callback_query=SimpleNamespace(id=f"cb-{len(env.bot.answered)}", data=cd))
            await env.callback_router.route(update, None)
            return
        await asyncio.sleep(0.02)
    raise AssertionError("consent prompt never appeared on Telegram")


async def _turn(env: _Env, text: str, *, tap: str) -> None:
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
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await _tap(env, tap)
    await run_task
    await out_task
    env.stream_registry.remove(msg.trace_id)


async def _build(tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[_Env, SkillIndexStore]:
    workspace = tmp_path / "workspace"
    (workspace / "skills" / "learned").mkdir(parents=True)
    monkeypatch.setenv("STACKOWL_DATA_DIR", str(workspace))

    async def _fake_reindex(loader, store_, skills_root, *, embedding_registry=None):  # noqa: ANN001, ANN202
        return []

    monkeypatch.setattr(sm, "reindex_after_change", _fake_reindex)
    store = SkillIndexStore(tmp_db)

    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""

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
    routing = RoutingPrompter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=5.0)
    routing.register("telegram", prompter)
    gate = ConsequentialActionGate(ConsentPolicy(prompter=routing, audit_logger=audit))
    router = CallbackRouter(tmp_db, adapter)
    await router.ensure_table()
    router.register("consent:", prompter.handle_callback)
    adapter.attach_callback_router(router)

    provider = _SkillProvider()
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        consent_gate=gate,
        stream_registry=StreamRegistry(),
        db_pool=tmp_db,
        skill_store=store,
    )
    env = _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        callback_router=router, provider=provider,
    )
    return env, store


async def test_smoke_skill_manage_create_via_telegram_consent(
    tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env, store = await _build(tmp_db, tmp_path, monkeypatch)

    await _turn(env, "create a polite greeting skill", tap="session")

    # 1) consent keyboard reached the user (consequential self-edit gated).
    kb = [m for m in env.bot.messages if m["reply_markup"] is not None]
    assert kb and kb[0]["chat_id"] == USER_ID
    # 2) only AFTER approval did the skill get created in the real store + on disk.
    assert "Created skill 'greet-politely'" in env.provider.result, env.provider.result
    md = StackowlHome.skills_dir() / "learned" / "greet-politely" / "SKILL.md"
    assert md.exists() and "greet-politely" in md.read_text(encoding="utf-8")
    # 3) the mutation is audited via the provenance chokepoint (restore net).
    audits = await store.recent_audit_for_skill("greet-politely")
    assert audits and audits[0].op == "create" and audits[0].snapshot


async def test_smoke_skill_manage_denied_does_not_create(
    tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env, store = await _build(tmp_db, tmp_path, monkeypatch)

    await _turn(env, "create a polite greeting skill", tap="deny_session")

    # Denied → the self-edit must NOT happen: no skill, no file, no audit.
    md = StackowlHome.skills_dir() / "learned" / "greet-politely" / "SKILL.md"
    assert not md.exists(), "skill created despite denied consent"
    assert await store.recent_audit_for_skill("greet-politely") == []

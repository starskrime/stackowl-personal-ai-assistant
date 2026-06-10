"""E4-S3/S4 SMOKE — skills_list + skill_view driven AS THE USER, Telegram → end.

A real inbound Telegram message traverses the GENUINE path (adapter → scanner →
AsyncioBackend → execute._dispatch → ToolRegistry → skills_list / skill_view)
against a REAL SkillIndexStore + tmp skills tree. Turn 1 lists skills; turn 2
views one (with a linked reference). Read tools — no consent round-trip.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import GatewayScanner
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest, SkillSource
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 878787


async def _seed(store: SkillIndexStore, workspace: Path, *, name: str, source: SkillSource,
                body: str, refs: dict[str, str] | None = None) -> None:
    skill_dir = workspace / "skills" / source / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} procedure\nenabled: true\n---\n\n{body}",
        encoding="utf-8",
    )
    if refs:
        (skill_dir / "references").mkdir(exist_ok=True)
        for fn, fb in refs.items():
            (skill_dir / "references" / fn).write_text(fb, encoding="utf-8")
    manifest = SkillManifest.model_validate(
        {"name": name, "description": f"{name} procedure", "source": source, "enabled": True, "tags": ["demo"]}
    )
    await store.upsert(LoadedSkill(manifest=manifest, path=skill_dir, body=body,
                                   tools_registered=0, owls_registered=0))


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
    _writer, reader = env.stream_registry.create(msg.trace_id)
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
    )
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.trace_id)
    return ""


async def test_smoke_skills_list_then_view_through_telegram(
    tmp_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    monkeypatch.setenv("STACKOWL_DATA_DIR", str(workspace))
    store = SkillIndexStore(tmp_db)
    await _seed(store, workspace, name="brew-coffee", source="builtin", body="## Steps\n\nBoil, pour.\n")
    await _seed(store, workspace, name="deploy", source="learned",
                body="## Steps\n\nShip it.\n", refs={"checklist.md": "# Checklist\n\n- tests green\n"})

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
        skill_store=store,
        db_pool=tmp_db,
    )
    env = _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider,
    )

    # Turn 1: list the available skills.
    provider.script.append(("skills_list", {}))
    await _turn(env, "what skills do you have")
    assert "brew-coffee" in provider.results[0] and "deploy" in provider.results[0]

    # Turn 2: view one skill (with its linked reference).
    provider.script.append(("skill_view", {"name": "learned:deploy"}))
    await _turn(env, "show me the deploy skill")
    assert "Ship it." in provider.results[1]
    assert "checklist.md" in provider.results[1]  # linked reference subloaded
    assert bot.messages and bot.messages[-1]["chat_id"] == USER_ID

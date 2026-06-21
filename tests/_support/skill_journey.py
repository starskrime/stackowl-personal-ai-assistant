"""Reusable end-to-end skill-journey harness + a RECORDING provider.

The provider is the only AI mock; every other piece (real SkillsAssembly store
from on-disk SKILL.md, real assemble/execute, real Telegram adapter transport,
real skills_list/skill_view tools) is production code. The recording provider
captures the system prompt and the tool roster it is actually handed, then runs a
caller-supplied *script* against the real tool dispatcher — so a test can prove
not just that skill text was rendered, but that the model could DISCOVER and CALL
a skill from the default owl's roster (the "registered ≠ reachable" guard).
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.settings import Settings
from stackowl.db.pool import DbPool
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult, Message
from stackowl.skills.assembly import SkillsAssembly
from stackowl.tools.knowledge.skill_view import SkillViewTool
from stackowl.tools.knowledge.skills_list import SkillsListTool
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 717171


# --------------------------------------------------------------------------- #
# Stub embedder (so SkillsAssembly.build can embed without a real model)
# --------------------------------------------------------------------------- #
@dataclass
class StubEmbeddingProvider:
    dim: int = 8
    model_name: str = "stub-embed-v1"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            vec = [0.0] * self.dim
            for tok in t.lower().split():
                digest = hashlib.sha1(tok.encode("utf-8")).digest()
                vec[digest[0] % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / n for x in vec])
        return out


@dataclass
class StubEmbeddingRegistry:
    provider: StubEmbeddingProvider = field(default_factory=StubEmbeddingProvider)

    def get(self) -> StubEmbeddingProvider:
        return self.provider


# --------------------------------------------------------------------------- #
# Faked Telegram transport
# --------------------------------------------------------------------------- #
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


# Script signature: async (dispatcher, provider) -> final reply text.
Script = Callable[[Callable[[str, dict], Awaitable[str]], "RecordingProvider"], Awaitable[str]]


class RecordingProvider:
    """Records the system prompt + presented roster, then runs a caller script."""

    protocol = "anthropic"

    def __init__(self, owl_name: str, script: Script, *, final: str = "Done.") -> None:
        self._owl_name = owl_name
        self._script = script
        self._final = final
        self.system_text: str = ""
        self.presented_tool_names: list[str] = []
        self.trace: list[str] = []

    @property
    def name(self) -> str:
        return self._owl_name

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None, **_kw
    ):
        self.system_text = system_text or ""
        self.presented_tool_names = [_schema_name(s) for s in (tool_schemas or [])]

        async def _dispatch(name: str, args: dict) -> str:
            self.trace.append(name)
            return await tool_dispatcher(name, args)

        reply = await self._script(_dispatch, self)
        return (reply or self._final, [])

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        return CompletionResult(
            content="ok", input_tokens=4, output_tokens=4, model="rec-model",
            provider_name=self._owl_name, duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover — not on this path
        if False:
            yield ""


class _FakeProviderRegistry:
    def __init__(self, p: RecordingProvider) -> None:
        self._p = p

    def get(self, name: str) -> RecordingProvider:
        return self._p

    def get_by_tier(self, tier: str) -> RecordingProvider:
        return self._p

    def get_with_cascade(self, preferred_tier: str) -> RecordingProvider:
        return self._p


def write_skill_md(
    skills_root: Path, source: str, name: str, *, description: str,
    category: str | None = None, when_to_use: str = "", summary: str | None = None,
    body: str = "Step 1. Do the thing.",
) -> None:
    """Emit a SKILL.md. When ``category`` is set, nests it <source>/<category>/<name>/."""
    d = skills_root / source / category / name if category else skills_root / source / name
    d.mkdir(parents=True, exist_ok=True)
    fm = [f"name: {name}", f"description: {description}"]
    if when_to_use:
        fm.append(f"when_to_use: {when_to_use}")
    if summary is not None:
        fm.append(f"summary: {summary}")
    (d / "SKILL.md").write_text(
        "---\n" + "\n".join(fm) + "\n---\n\n" + body + "\n", encoding="utf-8"
    )


async def build_store(db: DbPool, skills_root: Path):  # noqa: ANN202
    """Build a REAL SkillIndexStore over the on-disk SKILL.md tree."""
    components = await SkillsAssembly.build(
        db=db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=skills_root, builtin_seed_dir=skills_root / "no_builtins",
        embedding_registry=StubEmbeddingRegistry(),
    )
    return components.store


@dataclass
class JourneyEnv:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: RecordingProvider
    owl_registry: OwlRegistry
    tool_registry: ToolRegistry
    services: StepServices


def build_env(
    provider: RecordingProvider,
    *,
    skill_store: object,
    owl_registry: OwlRegistry,
    settings: Settings | None = None,
) -> JourneyEnv:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)  # type: ignore[assignment]
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    tool_registry = ToolRegistry()
    # The REAL discovery tools (read the store via get_services().skill_store).
    tool_registry.register(SkillsListTool())
    tool_registry.register(SkillViewTool())
    for extra in ("read_file", "memory", "web_fetch", "tool_search", "tool_describe"):
        tool_registry.register(_PassthroughTool(extra))

    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=tool_registry,
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        owl_registry=owl_registry,
        skill_store=skill_store,  # type: ignore[arg-type]
        embedding_registry=StubEmbeddingRegistry(),  # type: ignore[arg-type]
        settings=settings,
    )
    return JourneyEnv(
        adapter=adapter, bot=bot,
        backend=AsyncioBackend(services=services),  # type: ignore[arg-type]
        stream_registry=services.stream_registry, provider=provider,
        owl_registry=owl_registry, tool_registry=tool_registry, services=services,
    )


async def run_turn(env: JourneyEnv, text: str) -> str:
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    msg = await env.adapter.receive()
    from stackowl.gateway.scanner import GatewayScanner

    decision = GatewayScanner(owl_registry=env.owl_registry).scan(msg)
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


# Local import at bottom to avoid a heavy import when only helpers are used.
from stackowl.tools.base import Tool, ToolManifest, ToolResult  # noqa: E402


class _PassthroughTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"stub {self._name}"

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
        return ToolResult(success=True, output=f"OK:{self._name}", error=None, duration_ms=1.0)

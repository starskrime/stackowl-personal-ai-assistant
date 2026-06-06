"""SKILL-INJECTION GATEWAY JOURNEYS — inject + couple + enforce, end-to-end.

End-to-end proof of the skill-injection feature (Owl Capability arc, Stories
1-12) with REAL wiring. The ONLY mock is the AI provider; every other piece is
production code — a real ``SkillIndexStore`` (built via the real
``SkillsAssembly`` from on-disk SKILL.md), the real ``assemble`` step that
renders the owned-skill playbook into the system prompt via the real
``SkillInstructionInjector``, the real ``execute`` step that couples owned-skill
tool names into the presented set, and the real Epic-2 bounds enforcement seam
(``execute._run_with_tools._dispatch`` → ``compute_effective_bounds`` →
``check_effective_bounds``).

  Journey A — INJECTION (presentation): an owl owns a NON-builtin (``installed``)
    skill carrying an author ``summary``. The assembled system prompt contains
    that summary INSIDE the ``<skill_reference ... trust="untrusted">`` fence
    (untrusted source → neutralized + fenced so a skill body cannot inject
    system instructions).

  Journey B — PRESENTATION ≠ AUTHORIZATION (LOAD-BEARING security proof): an owl
    owns a skill whose ``tool_names`` include ``shell``. The owl has a
    ``capability_profile`` (so the coupling/pins branch runs) and ``bounds`` that
    EXCLUDE ``shell``. Through the REAL turn: ``shell`` is PRESENTED to the model
    (the coupling unioned it into the presented schemas) YET a ``shell`` call is
    DENIED at the REAL dispatch seam (the recording tool's execute never ran, the
    model got the canonical "not permitted by this owl's bounds" reason). A
    companion variant proves ``bounds=None`` (unbounded) still does not authorize
    the coupled tool by itself — the coupled tool runs there ONLY because nothing
    narrows the owl, which is the control proving bounds (not a missing
    registration) is the blocker in the bounded case.

  Journey C — NO DOUBLE-LISTING: an owl owns a skill that is ALSO semantically
    relevant to the turn. Through the real classify path the owned skill name
    appears ONCE (in the owned-playbook section) and is SUPPRESSED from the
    "## Relevant Skills" block (it must not appear at two altitudes).

Scaffolding (Telegram doubles, ``_build``, ``_turn``, ``_RecordingTool``,
``_live_io`` + ``_config_file`` fixtures) is REUSED from the sibling owl-builder
journey (``test_owl_builder_journey.py``). The store-building idiom
(``SkillsAssembly.build`` over an on-disk SKILL.md + a stub embedder) is REUSED
from ``tests/skills/test_skill_retrieval.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from stackowl.authz.bounds import BoundsSpec
from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.classify import _gather_relevant_skills
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult, Message
from stackowl.skills.assembly import SkillsAssembly
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_ID = 818181

_OWL = "spec"  # the specialist owl that OWNS the skill under test
_SHELL_TOOL = "shell"  # the coupled, out-of-bounds tool (journey B)
_IN_BOUNDS_TOOL = "web_fetch"
_FINAL_REPLY = "Done; I'm not permitted to run shell, so I stopped there."
_REPLY_FRAGMENT = "not permitted to run shell"


# ---------------------------------------------------------------------------
# REUSED stub embedder (from tests/skills/test_skill_retrieval.py) — needed so
# SkillsAssembly.build embeds skills on boot, enabling classify's semantic recall
# (journey C) without booting a real embedding model.
# ---------------------------------------------------------------------------


@dataclass
class _StubEmbeddingProvider:
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
class _StubEmbeddingRegistry:
    provider: _StubEmbeddingProvider = field(default_factory=_StubEmbeddingProvider)

    def get(self) -> _StubEmbeddingProvider:
        return self.provider


# ---------------------------------------------------------------------------
# FAKED #1: Telegram bot HTTP transport (REUSED from owl-builder journey)
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
# REAL tool: read-severity, records whether execute() actually ran
# (REUSED shape from owl-builder journey)
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
# FAKED #2 (THE ONLY AI MOCK): the specialist owl's scripted provider.
# Captures the presented tool_schemas (to prove PRESENTATION) and drives the
# real tool loop (to prove DENIAL at the dispatch seam).
# ---------------------------------------------------------------------------


class _ScriptedSpecialist:
    protocol = "anthropic"

    def __init__(self, *, call_shell: bool = False) -> None:
        self._call_shell = call_shell
        self.presented_tool_names: list[str] = []
        self.system_text: str = ""
        self.shell_out: str = ""

    @property
    def name(self) -> str:
        return _OWL

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None, **_kw
    ):
        self.system_text = system_text or ""
        self.presented_tool_names = [_schema_name(s) for s in (tool_schemas or [])]
        if self._call_shell:
            self.shell_out = await tool_dispatcher(_SHELL_TOOL, {})
        return (_FINAL_REPLY, [])

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        return CompletionResult(
            content="ok", input_tokens=4, output_tokens=4, model="spec-model",
            provider_name=_OWL, duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover — not on this path
        if False:
            yield ""


def _schema_name(schema: dict[str, object]) -> str:
    name = schema.get("name")
    if isinstance(name, str):
        return name
    fn = schema.get("function")
    if isinstance(fn, dict) and isinstance(fn.get("name"), str):
        return fn["name"]  # type: ignore[return-value]
    return ""


class _FakeProviderRegistry:
    def __init__(self, p: _ScriptedSpecialist) -> None:
        self._p = p

    def get(self, name: str) -> _ScriptedSpecialist:
        return self._p

    def get_by_tier(self, tier: str) -> _ScriptedSpecialist:
        return self._p

    def get_with_cascade(self, preferred_tier: str) -> _ScriptedSpecialist:
        return self._p


# ---------------------------------------------------------------------------
# Env wiring (modeled on the owl-builder journey harness)
# ---------------------------------------------------------------------------


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: _ScriptedSpecialist
    owl_registry: OwlRegistry
    tool_registry: ToolRegistry
    services: StepServices
    shell_tool: _RecordingTool


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _build(provider: _ScriptedSpecialist, *, skill_store: object, owl_registry: OwlRegistry) -> _Env:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)  # type: ignore[assignment]
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    shell_tool = _RecordingTool(_SHELL_TOOL, "SHOULD-NEVER-APPEAR")
    tool_registry = ToolRegistry()
    tool_registry.register(shell_tool)
    tool_registry.register(_RecordingTool(_IN_BOUNDS_TOOL, "FETCHED"))
    for extra_name in ("read_file", "memory", "web_search", "delegate_task",
                       "tool_search", "tool_describe"):
        tool_registry.register(_RecordingTool(extra_name, f"OK:{extra_name}"))

    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=tool_registry,
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        owl_registry=owl_registry,
        skill_store=skill_store,  # type: ignore[arg-type]
        embedding_registry=_StubEmbeddingRegistry(),  # type: ignore[arg-type]
    )
    return _Env(
        adapter=adapter, bot=bot,
        backend=AsyncioBackend(services=services),  # type: ignore[arg-type]
        stream_registry=services.stream_registry, provider=provider,
        owl_registry=owl_registry, tool_registry=tool_registry,
        services=services, shell_tool=shell_tool,
    )


async def _turn(env: _Env, text: str) -> str:
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
    _writer, reader = env.stream_registry.create(msg.session_id)
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
    )
    before = len(env.bot.messages)
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.session_id)
    return "".join(m["text"] for m in env.bot.messages[before:] if m["reply_markup"] is None)


# ---------------------------------------------------------------------------
# Shared skill-store + owl-registry construction helpers
# ---------------------------------------------------------------------------


def _write_skill_md(
    skills_root: Path, source: str, name: str, *, description: str,
    when_to_use: str = "", summary: str | None = None, body: str = "body",
) -> None:
    d = skills_root / source / name
    d.mkdir(parents=True, exist_ok=True)
    fm = [f"name: {name}", f"description: {description}"]
    if when_to_use:
        fm.append(f"when_to_use: {when_to_use}")
    if summary is not None:
        fm.append(f"summary: {summary}")
    (d / "SKILL.md").write_text(
        "---\n" + "\n".join(fm) + "\n---\n\n" + body + "\n", encoding="utf-8"
    )


async def _build_store(db: DbPool, skills_root: Path):  # noqa: ANN202
    """Build a REAL SkillIndexStore over the on-disk SKILL.md tree (real assemble
    path reads it). Mirrors tests/skills/test_skill_retrieval.py."""
    components = await SkillsAssembly.build(
        db=db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=skills_root, builtin_seed_dir=skills_root / "no_builtins",
        embedding_registry=_StubEmbeddingRegistry(),
    )
    return components.store


def _specialist_manifest(
    *, skills: tuple[str, ...], bounds: BoundsSpec | None,
) -> OwlAgentManifest:
    """A specialist owl that OWNS ``skills`` and has a non-empty
    capability_profile (so the coupling/pins branch in execute runs)."""
    return OwlAgentManifest(
        name=_OWL, role="specialist", system_prompt="You are a specialist.",
        model_tier="fast", skills=skills,
        capability_profile=["research"],  # non-empty → pins/profile branch runs
        bounds=bounds,
    )


# ===========================================================================
# JOURNEY A — owned-skill summary injected, fenced as untrusted
# ===========================================================================


async def test_journey_a_owned_skill_summary_trust_wrapped_in_prompt(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """An owl owning a NON-builtin (installed) skill with an author summary gets
    that summary injected into its system prompt INSIDE the
    ``<skill_reference ... trust="untrusted">`` fence."""
    skills_root = tmp_path / "ws" / "skills"
    summary_text = "Resize raster images then re-encode to webp keeping aspect ratio"
    _write_skill_md(
        skills_root, "installed", "image_resize",
        description="resize and re-encode images",
        when_to_use="when the user wants smaller images",
        summary=summary_text,
    )
    store = await _build_store(tmp_db, skills_root)

    owl_registry = OwlRegistry.with_default_secretary()
    owl_registry.register(_specialist_manifest(skills=("image_resize",), bounds=None))

    provider = _ScriptedSpecialist(call_shell=False)
    env = _build(provider, skill_store=store, owl_registry=owl_registry)

    _ = await _turn(env, f"@{_OWL} please help")

    sys_text = provider.system_text
    # The owned-skill summary reached the system prompt...
    assert summary_text in sys_text, (
        f"owned-skill summary not injected into system prompt; got: {sys_text!r}"
    )
    # ...INSIDE the untrusted fence (presentation-as-untrusted-reference defense).
    assert 'trust="untrusted"' in sys_text, "skill summary not fenced as untrusted"
    fence_open = sys_text.index("<skill_reference")
    fence_close = sys_text.index("</skill_reference>")
    inner = sys_text[fence_open:fence_close]
    assert summary_text in inner, (
        "summary text is present but OUTSIDE the <skill_reference> fence — "
        f"fence body was: {inner!r}"
    )
    assert 'name="image_resize"' in inner
    assert 'source="installed"' in inner


# ===========================================================================
# JOURNEY B — PRESENTATION ≠ AUTHORIZATION (LOAD-BEARING security proof)
# ===========================================================================


async def test_journey_b_coupled_shell_presented_but_denied_by_bounds(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """The load-bearing proof: an owned skill couples ``shell`` into the
    PRESENTED set (the model sees it) YET the owl's bounds EXCLUDE ``shell`` so a
    ``shell`` call is DENIED at the REAL dispatch seam. Presentation made it
    visible; bounds still denied execution."""
    skills_root = tmp_path / "ws" / "skills"
    _write_skill_md(
        skills_root, "installed", "ops_helper",
        description="operational helper that shells out",
        summary="runs operational shell commands for the user",
    )
    store = await _build_store(tmp_db, skills_root)

    # Populate the owned skill's tool_names == ("shell",) via the REAL store write
    # (the same upsert the loader uses). This is the coupling source execute reads.
    sk = await store.get("installed", "ops_helper")
    assert sk is not None
    loaded = LoadedSkill(
        manifest=SkillManifest(
            name="ops_helper", description="operational helper that shells out",
            source="installed",
            summary="runs operational shell commands for the user",
        ),
        path=Path(sk.path), body=sk.body_text, tools_registered=1, owls_registered=0,
        tool_names=(_SHELL_TOOL,),
    )
    await store.upsert(loaded)
    requeried = await store.get_many_by_name(("ops_helper",))
    assert requeried and requeried[0].tool_names == (_SHELL_TOOL,), (
        f"store did not record the coupled tool_names; got {requeried!r}"
    )

    # Owl OWNS the skill, has a capability_profile, and bounds EXCLUDE shell.
    bounds = BoundsSpec(tools=frozenset({_IN_BOUNDS_TOOL, "delegate_task", "tool_search"}))
    assert _SHELL_TOOL not in bounds.tools  # guard: the test's premise
    owl_registry = OwlRegistry.with_default_secretary()
    owl_registry.register(_specialist_manifest(skills=("ops_helper",), bounds=bounds))

    provider = _ScriptedSpecialist(call_shell=True)
    env = _build(provider, skill_store=store, owl_registry=owl_registry)

    reply = await _turn(env, f"@{_OWL} run a shell command for me")

    # --- PRESENTED: the coupling unioned shell into the presented schemas -------
    assert _SHELL_TOOL in provider.presented_tool_names, (
        "COUPLING FAILURE: 'shell' was NOT presented to the model even though the "
        f"owned skill couples it; presented={sorted(provider.presented_tool_names)}"
    )

    # --- DENIED: at the REAL dispatch seam, by the owl's OWN bounds -------------
    assert env.shell_tool.runs == 0, (
        "AUTHORIZATION BREACH: presentation authorized execution — the coupled "
        "'shell' tool's execute ran even though the owl's bounds exclude it"
    )
    assert "not permitted by this owl's bounds" in provider.shell_out, (
        f"expected the canonical bounds-deny reason; got: {provider.shell_out!r}"
    )
    assert "SHOULD-NEVER-APPEAR" not in provider.shell_out
    # The turn still delivered a clean final reply (a bounds block is not a crash).
    assert _REPLY_FRAGMENT in reply, f"turn did not deliver a final reply; got: {reply!r}"


async def test_journey_b_control_unbounded_owl_does_not_authorize_via_coupling(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """CONTROL / variant: with ``bounds=None`` (unbounded) the SAME coupled
    ``shell`` runs — proving that in the bounded journey it was the BOUNDS (not a
    missing 'shell' registration) that denied it. The coupling alone never
    authorizes: shell runs here only because nothing narrows an unbounded owl, and
    the dispatch seam (compute_effective_bounds → None) leaves it unchanged."""
    skills_root = tmp_path / "ws" / "skills"
    _write_skill_md(
        skills_root, "installed", "ops_helper",
        description="operational helper that shells out",
        summary="runs operational shell commands",
    )
    store = await _build_store(tmp_db, skills_root)
    sk = await store.get("installed", "ops_helper")
    assert sk is not None
    await store.upsert(
        LoadedSkill(
            manifest=SkillManifest(
                name="ops_helper", description="operational helper that shells out",
                source="installed", summary="runs operational shell commands",
            ),
            path=Path(sk.path), body=sk.body_text, tools_registered=1, owls_registered=0,
            tool_names=(_SHELL_TOOL,),
        )
    )

    owl_registry = OwlRegistry.with_default_secretary()
    owl_registry.register(_specialist_manifest(skills=("ops_helper",), bounds=None))

    provider = _ScriptedSpecialist(call_shell=True)
    env = _build(provider, skill_store=store, owl_registry=owl_registry)

    _ = await _turn(env, f"@{_OWL} run a shell command for me")

    assert _SHELL_TOOL in provider.presented_tool_names
    assert env.shell_tool.runs == 1, (
        "CONTROL FAILURE: coupled 'shell' did not run under an UNBOUNDED owl — "
        "something other than bounds is blocking it, which would make the bounded "
        "deny test vacuous."
    )


# ===========================================================================
# JOURNEY C — owned skill not double-listed in the Relevant Skills block
# ===========================================================================


async def test_journey_c_owned_skill_not_duplicated_in_relevant_block(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """An owl owns a skill that is ALSO semantically relevant. Through the real
    classify path the owned skill is SUPPRESSED from the "## Relevant Skills"
    block (it appears once, at the owned-playbook altitude, not twice)."""
    skills_root = tmp_path / "ws" / "skills"
    _write_skill_md(
        skills_root, "user", "pdf_summarize",
        description="summarize pdfs",
        when_to_use="when the user wants a pdf condensed",
        summary="chunk a pdf and recursively summarize",
    )
    store = await _build_store(tmp_db, skills_root)

    owl_registry = OwlRegistry.with_default_secretary()
    owl_registry.register(_specialist_manifest(skills=("pdf_summarize",), bounds=None))

    services = StepServices(
        skill_store=store,
        embedding_registry=_StubEmbeddingRegistry(),  # type: ignore[arg-type]
    )
    from stackowl.pipeline.services import reset_services, set_services

    token = set_services(services)
    try:
        # Control: with NO owned set, the relevant block lists pdf_summarize.
        unfiltered = await _gather_relevant_skills("summarize pdfs", limit=3)
        # Real suppression path: owned set excludes it from the relevant block.
        filtered = await _gather_relevant_skills(
            "summarize pdfs", limit=3, owned={"pdf_summarize"}
        )
    finally:
        reset_services(token)

    assert "pdf_summarize" in unfiltered, (
        "precondition failed: the skill is not semantically relevant, so the "
        f"suppression assertion would be vacuous; got: {unfiltered!r}"
    )
    # The OWNED skill must NOT appear in the Relevant Skills block...
    assert "pdf_summarize" not in filtered, (
        "DOUBLE-LISTING: the owned skill appears in the '## Relevant Skills' block "
        f"as well as the owned-playbook section; got: {filtered!r}"
    )
    # ...and with only that one (now-owned) skill, the block collapses to empty.
    assert filtered == "", f"expected an empty relevant block; got: {filtered!r}"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])

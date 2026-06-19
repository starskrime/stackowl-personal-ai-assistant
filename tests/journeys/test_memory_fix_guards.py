"""MEMORY-FIX JOURNEY GUARDS — wiring guards for the four memory fixes just landed.

Each guard drives the REAL path (channel/gateway → pipeline → execute →
ToolRegistry → real SqliteMemoryBridge over a tmp DB), mocking ONLY the AI
provider (and, where relevant, the external web egress), and asserts the
USER-FACING OUTCOME. Removing a production wire makes the corresponding guard
FAIL — that is the whole point.

Guarded fixes (see git log):
  * P0-1 (7cee822) — recall() falls back to FTS5 when semantic is empty.
  * P0-3 (0bfe3e2) — facts embedded + LanceDB-upserted at commit time.
  * P0-5 (6800756) — MemoryCommand (/memory) registered at startup.
  * P1-2 (f0bbf63) — `memory` pinned into _DEFAULT_BASE for every owl.
  * P2   (ff71219) — recent task_outcomes injected into classify (live recall).

The harness here REUSES the J2 journey infrastructure verbatim (``_build``,
``_turn``, ``_FakeBot``, the scripted-secretary contract, the ``_live_io``
fixture) so these guards exercise EXACTLY the wiring J2 proves, not a parallel
mock. We import those helpers rather than re-implement them.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.pipeline.state import PipelineState
from stackowl.tools.registry import ToolRegistry

# Reuse the J2 journey harness — same channel→gateway→pipeline→execute→registry
# →real-bridge wiring, same scripted-provider contract, same fake transport.
from tests.journeys.test_j2_research_and_remember import (  # noqa: F401 — fixture re-export
    USER_ID,
    _build,
    _Env,
    _FakeBot,
    _FakeBotApp,
    _FakeProviderRegistry,
    _live_io,  # autouse fixture: disables TestModeGuard for live I/O
    _turn,
)

# ===========================================================================
# GUARD B — recall surfaces a committed fact (P0-1 FTS fallback + P0-3 embed).
# ===========================================================================

# A distinctive fact the owl is told to remember on turn 1 and recall on turn 2.
# The value is a region slug that cannot appear unless it was stored and recalled.
_REGION_VALUE = "eu-west-1"
_REGION_FACT = f"my deploy region is {_REGION_VALUE}"


class _RememberThenRecallSecretary:
    """Scripted AI mock for Guard B.

    Turn 1: a natural "remember my deploy region" turn → call the REAL ``memory``
            tool (action=add) through the REAL tool_dispatcher, persisting the
            fact via the REAL bridge (stage → force_promote → committed_facts).
    Turn 2: "what's my deploy region?" — a real owl recalls from memory. It does
            NOT re-derive the answer; it reads what the REAL classify/assemble
            step folded into ``system_text`` and quotes it back. If recall
            regressed, the region is absent from system_text and the answer omits
            it (honest failure, no rescue).
    """

    protocol = "anthropic"
    name = "scripted-secretary"

    def __init__(self) -> None:
        self.turn = 0
        self.mem_out: str = ""
        self.turn2_system_text: str | None = None

    async def complete_with_tools(  # noqa: ANN001, ANN201
        self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None, **_kw
    ):
        self.turn += 1
        if self.turn == 1:
            self.mem_out = await tool_dispatcher(
                "memory", {"action": "add", "content": _REGION_FACT}
            )
            return (
                "Got it — I'll remember your deploy region.",
                [{"name": "memory", "args": {"action": "add"}, "result": self.mem_out}],
            )
        # Turn 2 — recall from the assembled context only.
        self.turn2_system_text = system_text
        surfaced = system_text or ""
        if history:
            surfaced += "\n" + "\n".join(getattr(m, "content", "") for m in history)
        if _REGION_VALUE in surfaced:
            answer = f"Your deploy region is {_REGION_VALUE}."
        else:
            answer = "I don't have your deploy region remembered yet."
        return (answer, [])

    async def complete(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        from stackowl.providers.base import CompletionResult

        return CompletionResult(
            content="", input_tokens=1, output_tokens=1, model="scripted",
            provider_name="scripted-secretary", duration_ms=0.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover  # noqa: ANN002, ANN003, ANN201
        if False:
            yield ""


def _build_with_provider(
    tmp_db: DbPool, provider: object, *, semantic_bridge_dir: object = None
) -> _Env:
    """Build the J2 env but swap in a custom scripted provider.

    When ``semantic_bridge_dir`` is given, the services' memory_bridge is wired
    WITH a LanceDB adapter pointed at that (empty) dir + a real embedding
    registry, so recall() actually ENTERS the semantic path and gets ``[]`` back
    (the LanceDB committed_facts table does not exist) — the exact condition P0-1
    guards. Without it, recall would skip the semantic branch and the P0-1 guard
    would be dead code.
    """
    env = _build(tmp_db)
    # Re-point the backend's provider registry at our scripted provider. The
    # backend resolves the provider via services.provider_registry.get*, so
    # swapping the registry is sufficient — every other wire stays REAL.
    env.backend._services.provider_registry = _FakeProviderRegistry(provider)  # type: ignore[attr-defined]
    env.provider = provider  # type: ignore[assignment]
    if semantic_bridge_dir is not None:
        from stackowl.embeddings.registry import EmbeddingRegistry
        from stackowl.memory.lancedb_adapter import LanceDBAdapter

        wired = SqliteMemoryBridge(
            tmp_db,
            embedding_registry=EmbeddingRegistry(),
            lancedb=LanceDBAdapter(data_dir=semantic_bridge_dir),
            semantic_search_enabled=True,
        )
        env.backend._services.memory_bridge = wired  # type: ignore[attr-defined]
    return env


async def test_guard_b_recall_surfaces_committed_fact(tmp_db: DbPool, tmp_path) -> None:  # noqa: ANN001
    """Remember a distinctive fact, then recall it — proves P0-1 + P0-3 wiring.

    Drives two REAL inbound Telegram turns through the genuine pipeline. The
    bridge is wired WITH a LanceDB adapter on an EMPTY dir + a real embedding
    registry, so recall() genuinely ENTERS the semantic path and gets ``[]`` back
    (the committed_facts LanceDB table does not exist) — the exact P0-1 trigger.
    Asserts the user OUTCOME (remember → recall): (a) the committed fact is
    retrievable via the REAL bridge.recall() (the production read path P0-1
    fixed — it must FALL THROUGH the empty semantic result to FTS5), AND (b) it
    flows into turn-2 system_text (classify→assemble recall) and reaches the
    user's chat. Reverting P0-1's ``if semantic:`` → ``if semantic is not None:``
    makes the empty semantic ``[]`` short-circuit recall → both the bridge
    assertion and the delivered-answer assertion fail.
    """
    provider = _RememberThenRecallSecretary()
    env = _build_with_provider(
        tmp_db, provider, semantic_bridge_dir=tmp_path / "empty_lancedb"
    )
    # The reader's LanceDB points at a SEPARATE empty dir (NOT the one the
    # turn-1 add upserts into), so its semantic path is GUARANTEED to return ``[]``
    # at recall time → the only way it can surface the fact is the FTS5 fallback
    # P0-1 fixed. (If it shared the add's dir, the upserted vector could satisfy
    # the semantic path and never exercise the fallback.)
    from stackowl.embeddings.registry import EmbeddingRegistry
    from stackowl.memory.lancedb_adapter import LanceDBAdapter

    reader_bridge = SqliteMemoryBridge(
        tmp_db,
        embedding_registry=EmbeddingRegistry(),
        lancedb=LanceDBAdapter(data_dir=tmp_path / "reader_empty_lancedb"),
        semantic_search_enabled=True,
    )

    # ---- TURN 1: remember --------------------------------------------------
    delivered1 = await _turn(env, "Remember that my deploy region is eu-west-1.")
    assert "Remembered" in provider.mem_out, (
        f"memory(add) did not confirm a store. Got: {provider.mem_out!r}"
    )
    assert delivered1, f"Turn 1 produced no reply. Delivered: {delivered1!r}"

    # ---- OUTCOME (a): the committed fact is recallable via the REAL bridge --
    # This is the production read path P0-1 fixed: semantic recall returns []
    # (no LanceDB table), and recall() must fall through to FTS5. On the OLD
    # ``if semantic is not None:`` guard, [] short-circuits and this returns [].
    recalled = await reader_bridge.recall("deploy region", limit=10)
    assert any(_REGION_VALUE in r.content for r in recalled), (
        "GUARD B FAIL: the committed deploy-region fact was NOT recallable via the "
        f"REAL bridge.recall() — P0-1 FTS fallback regressed. recall() returned: "
        f"{[r.content for r in recalled]!r}"
    )
    committed = await reader_bridge.list_staged(status="committed")
    assert any(
        _REGION_VALUE in f.content and f.source_type == "agent_self" for f in committed
    ), (
        "GUARD B FAIL: fact not a committed agent_self fact. committed: "
        f"{[(f.source_type, f.content) for f in committed]!r}"
    )

    # ---- TURN 2: recall, over the SAME db/bridge ---------------------------
    delivered2 = await _turn(env, "What's my deploy region?")
    delivered2_unescaped = delivered2.replace("\\", "")
    assert _REGION_VALUE in delivered2_unescaped, (
        "GUARD B FAIL: the recalled region did NOT reach the user's chat. "
        f"Delivered: {delivered2!r} | turn-2 system_text: {provider.turn2_system_text!r}"
    )
    # Belt-and-suspenders: the REAL classify→assemble path folded it into system_text.
    assert provider.turn2_system_text and _REGION_VALUE in provider.turn2_system_text, (
        "GUARD B FAIL: recall path did not surface the fact into turn-2 system_text. "
        f"Got: {provider.turn2_system_text!r}"
    )


# ===========================================================================
# GUARD REMEMBER — agentic remember reaches committed via the tool, even with a
# restrictive profile (P1-2 base-pin + P0-3).
# ===========================================================================

_BROWSER_OWL = "browserowl"
_SECRET_VALUE = "octopus-garden-1979"
_SECRET_FACT = f"my favorite passphrase is {_SECRET_VALUE}"


class _BrowserProfileRememberSecretary:
    """Scripted AI mock for the base-pin guard.

    On a natural "remember ..." turn it (1) RECORDS the tool_schemas it was
    PRESENTED (so the guard can assert ``memory`` is in the presented set even
    though this owl's capability_profile=["browser"] excludes the knowledge
    group), and (2) calls the REAL ``memory`` tool (action=add) to persist.
    """

    protocol = "anthropic"
    name = "scripted-secretary"

    def __init__(self) -> None:
        self.presented_tool_names: list[str] = []
        self.mem_out: str = ""

    async def complete_with_tools(  # noqa: ANN001, ANN201
        self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None, **_kw
    ):
        self.presented_tool_names = [
            str(s.get("name")) for s in (tool_schemas or []) if isinstance(s, dict)
        ]
        self.mem_out = await tool_dispatcher(
            "memory", {"action": "add", "content": _SECRET_FACT}
        )
        return (
            "Noted — I've remembered your passphrase.",
            [{"name": "memory", "args": {"action": "add"}, "result": self.mem_out}],
        )

    async def complete(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        from stackowl.providers.base import CompletionResult

        return CompletionResult(
            content="", input_tokens=1, output_tokens=1, model="scripted",
            provider_name="scripted-secretary", duration_ms=0.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover  # noqa: ANN002, ANN003, ANN201
        if False:
            yield ""


async def _turn_as_owl(env: _Env, text: str, owl_name: str) -> str:
    """Drive one real inbound Telegram turn, forcing the routed owl name.

    Identical to the J2 ``_turn`` except the PipelineState owl_name is the given
    owl (the scanner defaults to "secretary"; here we target a specific owl so
    execute() applies THAT owl's capability_profile when presenting tools).
    """
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
        trace_id=msg.trace_id,
        session_id=msg.session_id,
        input_text=input_text,
        channel=msg.channel,
        owl_name=owl_name,  # force the browser-profile owl
        pipeline_step="start",
    )
    before = len(env.bot.messages)
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.trace_id)
    return "\n".join(
        m["text"] for m in env.bot.messages[before:] if m["chat_id"] == USER_ID
    )


async def test_guard_remember_base_pin_reaches_memory_for_browser_owl(
    tmp_db: DbPool,
) -> None:
    """A browser-profile owl can still remember — proves P1-2 base-pin + P0-3.

    The owl's capability_profile=["browser"] EXCLUDES the knowledge group that
    owns ``memory``; memory is reachable ONLY because P1-2 pinned it into
    _DEFAULT_BASE. The guard asserts the user OUTCOME (the remembered passphrase
    becomes a committed agent_self fact, read back via an INDEPENDENT bridge),
    AND that ``memory`` was in the PRESENTED tool set for this profile (the
    direct artifact of the base-pin). Reverting the base-pin drops ``memory``
    from the presented set for a browser owl → the presented-set assertion fails.
    """
    provider = _BrowserProfileRememberSecretary()
    env = _build_with_provider(tmp_db, provider)
    # Register a browser-profile owl (does NOT pin memory; excludes knowledge).
    env.backend._services.owl_registry.register(  # type: ignore[attr-defined]
        OwlAgentManifest(
            name=_BROWSER_OWL,
            role="browser-specialist",
            system_prompt="You are a browser specialist.",
            model_tier="powerful",
            tools=[],  # no per-owl pins → memory must come from the BASE set
            capability_profile=["browser"],
        )
    )
    reader_bridge = SqliteMemoryBridge(db=tmp_db)  # independent reader, same DB

    delivered = await _turn_as_owl(env, "Please remember my favorite passphrase.", _BROWSER_OWL)

    # OUTCOME: the passphrase is a committed agent_self fact (the real store ran).
    committed = await reader_bridge.list_staged(status="committed")
    assert any(
        _SECRET_VALUE in f.content and f.source_type == "agent_self" for f in committed
    ), (
        "GUARD REMEMBER FAIL: the remembered passphrase is not a committed agent_self "
        f"fact. committed: {[(f.source_type, f.content) for f in committed]!r} | "
        f"mem_out={provider.mem_out!r}"
    )
    assert "Remembered" in provider.mem_out, (
        f"memory(add) did not confirm a store. Got: {provider.mem_out!r}"
    )
    assert delivered, f"browser owl produced no reply. Delivered: {delivered!r}"

    # DIRECT base-pin artifact: ``memory`` was PRESENTED to a browser-profile owl.
    # This is what the base-pin guarantees; reverting it removes memory from the
    # presented set (knowledge group is excluded by the browser profile).
    assert "memory" in provider.presented_tool_names, (
        "GUARD REMEMBER FAIL: ``memory`` was NOT presented to a browser-profile owl — "
        "the P1-2 base-pin regressed. Presented set: "
        f"{sorted(provider.presented_tool_names)!r}"
    )


def test_guard_remember_base_pin_in_presentation_directly() -> None:
    """Focused presentation-layer guard: to_provider_schema(profile=["browser"])
    includes ``memory`` purely because of the _DEFAULT_BASE pin.

    Complements the journey guard above: it isolates the exact line P1-2 changed
    (the base set) at the seam the journey relies on. Reverting the base-pin
    drops ``memory`` from this set (knowledge is not in the browser profile).
    """
    reg = ToolRegistry.with_defaults()
    presented = {
        str(s.get("name"))
        for s in reg.to_provider_schema("anthropic", profile=["browser"], pins=[])
    }
    assert "memory" in presented, (
        "GUARD REMEMBER (presentation) FAIL: ``memory`` is not in the browser-profile "
        f"presented set — base-pin regressed. Presented: {sorted(presented)!r}"
    )


# ===========================================================================
# GUARD ACTIONS — live action recall (P2).
# ===========================================================================

_ACTION_FACT_QUERY = "look up the current ARM64 ML inference landscape"


class _ActOnceThenRecallSecretary:
    """Scripted AI mock for the live-action-recall guard.

    Turn 1: call the REAL ``web_search`` tool (writes a task_outcomes row with
            tool_sequence=["web_search"] via the backend's _capture_outcome) and
            reply — this is the action the agent should later be able to recall.
    Turn 2: "what did you just do?" — reads the REAL classify-built memory_context
            (which P2 injects "## What You Did Recently" into) from system_text
            and quotes it back.
    """

    protocol = "anthropic"
    name = "scripted-secretary"

    def __init__(self) -> None:
        self.turn = 0
        self.turn2_system_text: str | None = None

    async def complete_with_tools(  # noqa: ANN001, ANN201
        self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None, **_kw
    ):
        self.turn += 1
        if self.turn == 1:
            out = await tool_dispatcher(
                "web_search", {"query": "ARM64 ML inference", "limit": 3}
            )
            return (
                "I searched the web for the ARM64 ML inference landscape.",
                [{"name": "web_search", "args": {"query": "ARM64 ML inference"}, "result": out}],
            )
        self.turn2_system_text = system_text
        surfaced = system_text or ""
        if "What You Did Recently" in surfaced and "web_search" in surfaced:
            answer = "Just now I ran a web_search for the ARM64 ML inference landscape."
        else:
            answer = "I don't have a record of what I just did."
        return (answer, [])

    async def complete(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        from stackowl.providers.base import CompletionResult

        return CompletionResult(
            content="", input_tokens=1, output_tokens=1, model="scripted",
            provider_name="scripted-secretary", duration_ms=0.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover  # noqa: ANN002, ANN003, ANN201
        if False:
            yield ""


async def test_guard_actions_live_recall_of_recent_action(tmp_db: DbPool) -> None:
    """"What did you just do?" surfaces the prior turn's tool — proves P2 wiring.

    Two REAL turns, same session. Turn 1 runs a real tool (web_search) through
    the genuine pipeline; the AsyncioBackend's _capture_outcome persists a
    task_outcomes row over the tmp DB (confirmed below). Turn 2 asks what the
    agent did; classify._gather_recent_actions reads that row back and injects a
    "## What You Did Recently" block (with the tool name) into memory_context →
    system_text. The guard asserts both appear in turn-2 system_text. Reverting
    the P2 wire (the actions_block in classify) removes the block → fails.
    """
    provider = _ActOnceThenRecallSecretary()
    env = _build_with_provider(tmp_db, provider)

    # ---- TURN 1: do something (a real tool call) ---------------------------
    delivered1 = await _turn(env, "Search the web for the ARM64 ML inference landscape.")
    assert env.web_provider.calls >= 1, (
        f"Turn 1 never hit the web backend — web_search did not run. delivered1={delivered1!r}"
    )

    # Confirm the harness genuinely persisted a task_outcomes row for turn 1
    # (the prior-turn record P2 reads). This is the REAL _capture_outcome path.
    store = TaskOutcomeStore(tmp_db)
    # The Telegram adapter uses session_id = str(user_id); turns share it.
    msg_session = str(USER_ID)
    outcomes = await store.recent_for_session(msg_session, limit=5)
    assert any("web_search" in o.tool_sequence for o in outcomes), (
        "GUARD ACTIONS PRECONDITION FAIL: turn-1 outcome with web_search was not "
        f"persisted to task_outcomes — _capture_outcome did not run. outcomes: "
        f"{[(o.session_id, o.tool_sequence) for o in outcomes]!r}"
    )

    # ---- TURN 2: ask what you just did -------------------------------------
    delivered2 = await _turn(env, "What did you just do?")

    # OUTCOME: the recall block + the prior tool name reached the model's prompt.
    sys2 = provider.turn2_system_text or ""
    assert "What You Did Recently" in sys2, (
        "GUARD ACTIONS FAIL: the '## What You Did Recently' block was NOT injected into "
        f"turn-2 system_text — P2 classify wiring regressed. system_text: {sys2!r}"
    )
    assert "web_search" in sys2, (
        "GUARD ACTIONS FAIL: the prior turn's tool name (web_search) was NOT in the "
        f"recent-actions block. system_text: {sys2!r}"
    )
    assert delivered2, f"Turn 2 produced no reply. Delivered: {delivered2!r}"


# ===========================================================================
# GUARD MEMORY COMMAND — /memory is registered through the orchestrator path (P0-5).
# ===========================================================================


async def test_guard_memory_command_registered_via_orchestrator(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The startup orchestrator registers /memory — proves P0-5 wiring.

    There is already ``tests/test_memory_command_registration.py`` calling
    ``MemoryCommand.create_and_register`` DIRECTLY. This guard goes one level up:
    it drives the orchestrator's REAL ``_phase_gateway`` registration code so the
    guard bites if the orchestrator stops calling create_and_register (the exact
    P0-5 regression). Full boot is impractical in a bounded test (it opens the
    real ~/.stackowl DB, builds Kuzu/LanceDB/providers/browser and then BLOCKS in
    the message loop), so we run _phase_gateway with the heavy collaborators
    stubbed and SHORT-CIRCUIT immediately AFTER the registration call (which sits
    just before NotificationAssembly.build) by making that build raise a sentinel.
    Everything up to and including the real MemoryCommand registration runs.

    Assertions (the user OUTCOME of /memory being wired):
      1. after the orchestrator's registration path runs, the CommandRegistry
         singleton resolves ``memory``, and dispatching ``/memory remember X`` +
         ``/memory search X`` persists and recalls X over the tmp DB;
      2. a guard that a FRESH (reset) registry does NOT resolve ``memory`` —
         proving the registration is what wires it, not ambient state.
    """
    from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
    from stackowl.memory.fact_promoter import FactPromoter
    from stackowl.startup import orchestrator as orch_mod

    # --- (2) NEGATIVE GUARD FIRST: a fresh registry does NOT resolve /memory ---
    CommandRegistry.reset()
    fresh = CommandRegistry.instance()
    assert not any(c.command == "memory" for c in fresh.list()), (
        "pre-condition: fresh registry must not already have /memory"
    )

    # --- Stub the heavy collaborators _phase_gateway builds BEFORE registration.
    # Each is replaced with the lightest real/fake object the registration needs.
    # The REAL CommandRegistry + REAL bridge/promoter over tmp_db are used, so the
    # dispatch assertions exercise genuine persist+recall.
    bridge = SqliteMemoryBridge(db=tmp_db)
    promoter = FactPromoter(db=tmp_db)

    memory_components = SimpleNamespace(
        bridge=bridge,
        preference_store=SimpleNamespace(),
        kuzu_adapter=SimpleNamespace(),
        embedding_registry=None,
        lancedb=getattr(bridge, "lancedb", None),
        promoter=promoter,
        lessons_index=SimpleNamespace(),
    )

    class _SentinelStop(Exception):
        """Raised right AFTER registration to halt the monolithic _phase_gateway."""

    async def _fake_memory_build(*_a: object, **_k: object) -> object:
        return memory_components

    async def _fake_skills_build(*_a: object, **_k: object) -> object:
        return SimpleNamespace(store=SimpleNamespace(), loader=SimpleNamespace(), loaded=[])

    async def _fake_notif_build(*_a: object, **_k: object) -> object:
        # Registration now happens via the single register_all_commands call,
        # which runs AFTER NotificationAssembly.build + SchedulerAssembly.build
        # (Epic A/B spine). So this no longer raises — it returns a minimal
        # NotificationComponents-shaped fake (the orchestrator reads .router +
        # .proactive_deliverer, the latter passed into ParliamentOrchestrator).
        return SimpleNamespace(router=SimpleNamespace(), proactive_deliverer=SimpleNamespace())

    async def _fake_scheduler_build(*_a: object, **_k: object) -> object:
        # register_all_commands reads .scheduler + .morning_brief_handler.
        return SimpleNamespace(
            scheduler=SimpleNamespace(),
            morning_brief_handler=SimpleNamespace(),
            supervisor=SimpleNamespace(),
        )

    # Wrap the SINGLE registration entry: run the REAL registration (so /memory
    # actually lands on the registry over tmp_db), then halt the boot. The
    # orchestrator does `from stackowl.commands.assembly import register_all_commands`
    # at call time, so patching the source-module attribute is what binds.
    import stackowl.commands.assembly as _asm_mod

    _real_register = _asm_mod.register_all_commands

    def _wrapped_register(deps: object, registry: object = None) -> object:
        result = _real_register(deps, registry)  # type: ignore[arg-type]
        raise _SentinelStop  # reached only AFTER real registration ran
        return result  # pragma: no cover

    async def _fake_learned_load(_self: object, _reg: object) -> int:
        return 0

    def _fake_skill_register(*_a: object, **_k: object) -> None:
        return None

    # Patch at the SOURCE modules (the method imports them locally at call time).
    monkeypatch.setattr("stackowl.memory.assembly.MemoryAssembly.build", _fake_memory_build)
    monkeypatch.setattr("stackowl.skills.assembly.SkillsAssembly.build", _fake_skills_build)
    monkeypatch.setattr(
        "stackowl.notifications.assembly.NotificationAssembly.build", _fake_notif_build
    )
    monkeypatch.setattr(
        "stackowl.scheduler.assembly.SchedulerAssembly.build", _fake_scheduler_build
    )
    monkeypatch.setattr(
        "stackowl.commands.assembly.register_all_commands", _wrapped_register
    )
    monkeypatch.setattr(
        "stackowl.tools.meta.learned_tool_loader.LearnedToolLoader.load_all",
        _fake_learned_load,
    )
    monkeypatch.setattr(
        "stackowl.commands.skill_command.SkillCommand.create_and_register",
        _fake_skill_register,
    )
    # Point the orchestrator's DB at the tmp DB instead of ~/.stackowl, and make a
    # second open() a no-op (tmp_db is already open).
    monkeypatch.setattr(orch_mod, "default_db_path", lambda: tmp_db._path)  # noqa: SLF001

    class _AlreadyOpenPool:
        def __init__(self, _path: object) -> None:
            self._inner = tmp_db

        async def open(self) -> None:
            return None

        def __getattr__(self, item: str) -> object:
            return getattr(self._inner, item)

    monkeypatch.setattr(orch_mod, "DbPool", _AlreadyOpenPool)
    # AuditLogger ctor takes a path; harmless over tmp path. MCP disabled by default
    # in test settings (no servers configured), so the MCP block is skipped.

    orchestrator = orch_mod.StartupOrchestrator(dry_run=False)
    orchestrator._settings = orch_mod.Settings()  # type: ignore[attr-defined]
    orchestrator._browser_probe_result = None  # type: ignore[attr-defined] — skip browser

    # Run the REAL _phase_gateway; it self-halts at the sentinel just past
    # MemoryCommand registration. Any OTHER exception is a real failure.
    with pytest.raises(_SentinelStop):
        await orchestrator._phase_gateway()

    # --- (1) the orchestrator path registered /memory on the singleton ---------
    registry = CommandRegistry.instance()
    assert any(c.command == "memory" for c in registry.list()), (
        "GUARD P0-5 FAIL: the orchestrator did NOT register /memory — MemoryCommand."
        "create_and_register is not called in _phase_gateway."
    )

    from tests._story_6_7_helpers import make_state  # registry dispatch state

    marker = "the deploy bastion host is bastion-prod-7"
    remember_out = await registry.dispatch("memory", f"remember {marker}", make_state())
    assert remember_out.startswith("✓ Remembered"), remember_out

    # Persistence via an INDEPENDENT bridge over the same tmp DB.
    independent = SqliteMemoryBridge(db=tmp_db)
    committed = await independent.list_staged(status="committed")
    assert any(marker in f.content for f in committed), (
        f"GUARD P0-5 FAIL: /memory remember did not persist. committed: "
        f"{[f.content for f in committed]!r}"
    )

    search_out = await registry.dispatch("memory", "search bastion", make_state())
    assert "bastion-prod-7" in search_out, (
        f"GUARD P0-5 FAIL: /memory search did not recall the fact. Got: {search_out!r}"
    )

    # Sanity: an UNregistered fresh registry refuses the dispatch (registration is
    # what wires it).
    CommandRegistry.reset()
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("memory", f"remember {marker}", make_state())

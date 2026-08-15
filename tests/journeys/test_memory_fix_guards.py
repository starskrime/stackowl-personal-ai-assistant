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
                "memory",
                    # durability is REQUIRED since D08.1 — a write that cannot say
                    # how long it stays true has nowhere to go.
                    {"action": "add", "content": _REGION_FACT, "durability": "permanent"},
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
        # This wired a bridge with a LanceDB adapter at a chosen directory, so a
        # test could force the semantic path to return []. D08.2 removed that path
        # entirely; the parameter is kept so callers read unchanged, and now only
        # swaps in an independent bridge over the same database.
        from stackowl.embeddings.registry import EmbeddingRegistry

        wired = SqliteMemoryBridge(tmp_db, embedding_registry=EmbeddingRegistry())
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
    # An independent reader bridge with its own empty LanceDB used to stand here,
    # to force recall() through the FTS5 fallback P0-1 fixed. Both the reader and
    # the fallback assertion are gone with the committed_facts read path — see
    # OUTCOME (a) below and ESC-6.

    # ---- TURN 1: remember --------------------------------------------------
    delivered1 = await _turn(env, "Remember that my deploy region is eu-west-1.")
    assert "Saved." in provider.mem_out, (
        f"memory(add) did not confirm a store. Got: {provider.mem_out!r}"
    )
    assert delivered1, f"Turn 1 produced no reply. Delivered: {delivered1!r}"

    from stackowl.memory.curated import USER_TARGET, CuratedMemory

    # ---- OUTCOME (a): what was remembered is FINDABLE ----------------------
    # This asserted bridge.recall() falling through to FTS5 (the P0-1 fix, when
    # semantic returns [] because there is no LanceDB table). That read path is
    # over committed_facts, which has held 0 rows since D08.1's migration 0112 —
    # so the assertion could only ever fail once the write moved to curated
    # memory. The GUARANTEE it protects, that a remembered thing can be found
    # again, is live and now belongs to curated search.
    #
    # Whether the committed_facts read path keeps a guard of its own, given
    # nothing can write to it, is ESC-6 — deliberately not decided here.
    found = CuratedMemory().search("deploy region")
    assert any(_REGION_VALUE in text for _target, text in found), (
        "GUARD B FAIL: the remembered deploy-region fact was NOT findable via "
        f"curated search. search() returned: {found!r}"
    )
    # The STORE moved, the guarantee did not. memory(add) wrote committed_facts
    # until D08.1 retargeted the tool at curated memory; what this guard is for —
    # that the write actually lands somewhere durable rather than the agent merely
    # saying it remembered — is unchanged, and curated memory is where the system
    # prompt will read it.
    profile = [e.text for e in CuratedMemory().entries(USER_TARGET)]
    assert any(_REGION_VALUE in text for text in profile), (
        "GUARD B FAIL: fact not persisted to curated memory. profile: "
        f"{profile!r}"
    )

    # ---- TURN 2: recall, over the SAME db/bridge ---------------------------
    delivered2 = await _turn(env, "What's my deploy region?")
    delivered2_unescaped = delivered2.replace("\\", "")
    assert _REGION_VALUE in delivered2_unescaped, (
        "GUARD B FAIL: the recalled region did NOT reach the user's chat. "
        f"Delivered: {delivered2!r} | turn-2 system_text: {provider.turn2_system_text!r}"
    )
    # ---- OUTCOME (b): the prompt does NOT move mid-conversation -------------
    # INVERTED, deliberately. This used to assert the fact reached turn-2's
    # system_text. D08.1 made curated memory enter the prompt from a snapshot
    # frozen per incarnation — `curated.py:290` keys the cache on session_id, so
    # turn 2 re-reads the snapshot taken BEFORE turn 1's write. That is Law 1:
    # nothing mutates past context mid-conversation, because doing so would void
    # the prompt cache for every turn already in the window.
    #
    # So the OLD assertion now describes a cache-invalidating bug, and would pass
    # only if one existed. Asserting the absence is what protects the law.
    assert provider.turn2_system_text, "turn 2 produced no system_text to check"
    assert _REGION_VALUE not in provider.turn2_system_text, (
        "GUARD B FAIL: a mid-conversation write MOVED the frozen prompt — Law 1 "
        "broken, every cached turn in this window is now invalid. system_text: "
        f"{provider.turn2_system_text!r}"
    )

    # ---- OUTCOME (c): and it is not stranded either -------------------------
    # The other half of the same law, and the one that makes (b) safe rather than
    # merely quiet: frozen for THIS conversation, present for the next. Without
    # this, a write that never reaches any prompt would satisfy (b) perfectly —
    # the write-with-no-reader shape this programme keeps finding.
    # A FRESH CuratedMemory, not shared_memory(): the shared one is a PROCESS
    # singleton whose snapshot cache is keyed on session_id, so two tests using the
    # same session_id literal would serve each other stale snapshots across
    # different temp homes. A fresh instance reads the current files.
    next_incarnation = CuratedMemory().snapshot_for_prompt(
        USER_TARGET, session_id="guard-b-next-incarnation"
    )
    assert _REGION_VALUE in next_incarnation, (
        "GUARD B FAIL: the remembered fact never reaches the prompt at all — "
        f"a new incarnation's USER block is: {next_incarnation!r}"
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
            "memory",
                # durability is REQUIRED since D08.1 — a write that cannot say
                # how long it stays true has nowhere to go.
                {"action": "add", "content": _SECRET_FACT, "durability": "permanent"},
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
        session_key=msg.session_key,
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
    delivered = await _turn_as_owl(env, "Please remember my favorite passphrase.", _BROWSER_OWL)

    # OUTCOME: the passphrase is persisted to CURATED memory (the real store ran).
    # It was a committed agent_self fact until D08.1 retargeted the tool; the
    # guarantee under test — a base-pinned owl's remember actually reaches memory
    # — is the same one.
    from stackowl.memory.curated import USER_TARGET, CuratedMemory

    profile = [e.text for e in CuratedMemory().entries(USER_TARGET)]
    assert any(_SECRET_VALUE in text for text in profile), (
        "GUARD REMEMBER FAIL: the remembered passphrase did not reach curated "
        f"memory. profile: {profile!r} | mem_out={provider.mem_out!r}"
    )
    assert "Saved." in provider.mem_out, (
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
    # The Telegram adapter uses session_key = str(user_id); turns share it.
    msg_session = str(USER_ID)
    outcomes = await store.recent_for_session(msg_session, limit=5)
    assert any("web_search" in o.tool_sequence for o in outcomes), (
        "GUARD ACTIONS PRECONDITION FAIL: turn-1 outcome with web_search was not "
        f"persisted to task_outcomes — _capture_outcome did not run. outcomes: "
        f"{[(o.session_key, o.tool_sequence) for o in outcomes]!r}"
    )

    # ---- TURN 2: ask what you just did -------------------------------------
    delivered2 = await _turn(env, "What did you just do?")

    # OUTCOME: the recall block + the prior tool name reached the model's prompt.
    sys2 = provider.turn2_system_text or ""
    # INVERTED, deliberately. This used to assert the "## What You Did Recently"
    # block reached turn-2's system_text, and its failure message accused "P2
    # classify wiring" of regressing. It had not: D01.1 SPLIT the context, and
    # classify.py says so at the seam — "the STABLE half, carried into the system
    # prompt while the query-scoped half (recall, graph, actions, per-turn
    # skills) does not". Recent actions are query-scoped by design, because a
    # prompt that changes every turn forfeits the provider's prefix cache with no
    # marker to blame.
    #
    # So the guard now protects the CURRENT invariant, which is Law 1: the
    # actions block must stay OUT of the system prompt. The actions themselves
    # are still gathered — assemble.py notes memory_context "is still computed by
    # classify and still read by execute for its grounding haystacks" — they just
    # do not move the prompt.
    assert "What You Did Recently" not in sys2, (
        "LAW 1 REGRESSION: the recent-actions block is back in system_text. "
        "D01.1 moved it out on purpose; an unstable prompt loses the prefix "
        f"cache silently. system_text: {sys2!r}"
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
    from stackowl.startup import orchestrator as orch_mod

    # --- (2) NEGATIVE GUARD FIRST: a fresh registry does NOT resolve /memory ---
    CommandRegistry.reset()
    fresh = CommandRegistry.instance()
    assert not any(c.command == "memory" for c in fresh.list()), (
        "pre-condition: fresh registry must not already have /memory"
    )

    # --- Stub the heavy collaborators _phase_gateway builds BEFORE registration.
    # Each is replaced with the lightest real/fake object the registration needs.
    # The REAL CommandRegistry + REAL bridge over tmp_db are used, so the dispatch
    # assertions exercise genuine persist+recall.
    #
    # The SimpleNamespace below mirrors MemoryComponents, so it has to TRACK it: the
    # `promoter` field went with FactPromoter in D08.2 seam 3 pass 4, and a double
    # still carrying one would describe a shape production no longer has — the
    # test-doubles-drift failure mode.
    bridge = SqliteMemoryBridge(db=tmp_db)

    memory_components = SimpleNamespace(
        bridge=bridge,
        preference_store=SimpleNamespace(),
        kuzu_adapter=SimpleNamespace(),
        embedding_registry=None,
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
        # Task 7 (incident-escalation RCA wiring) also reads
        # .incident_escalation_handler unconditionally right after this
        # build() call returns (orchestrator.py) — omitting it here crashes
        # the fake boot with AttributeError before this guard ever reaches
        # its real assertions.
        return SimpleNamespace(
            scheduler=SimpleNamespace(),
            morning_brief_handler=SimpleNamespace(),
            supervisor=SimpleNamespace(),
            incident_escalation_handler=SimpleNamespace(),
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

    # A marker that ANNOUNCES ITSELF as test data. The old one — a plausible
    # "deploy bastion host" fact — escaped into the operator's real USER.md when
    # this file ran unisolated, and read as a genuine fact about him in every
    # system prompt until it was found. If isolation ever regresses again, the
    # leak should be obvious on sight.
    marker = "TEST-FIXTURE-DO-NOT-TRUST alpha-quebec-7"
    remember_out = (await registry.dispatch("memory", f"remember {marker}", make_state())).text
    # "Saved.", not "Remembered": D08.1 changed the confirmation when /memory was
    # retargeted at curated memory, and the second half of that message — when it
    # reaches the prompt — is the part that matters to the user.
    assert remember_out.startswith("✓ Saved."), remember_out

    # Persistence via an INDEPENDENT bridge over the same tmp DB.
    # Persistence is checked through an INDEPENDENT reader, as before — only the
    # STORE changed. /memory remember wrote committed_facts until D08.1 retargeted
    # the command at curated memory (abb08e09); the guard's intent, that the
    # command is registered AND genuinely persists rather than merely replying,
    # is unchanged and is what this still proves.
    from stackowl.memory.curated import USER_TARGET, CuratedMemory

    independent_profile = CuratedMemory()
    persisted = [e.text for e in independent_profile.entries(USER_TARGET)]
    assert any(marker in text for text in persisted), (
        f"GUARD P0-5 FAIL: /memory remember did not persist to curated memory. "
        f"profile: {persisted!r}"
    )

    search_out = (await registry.dispatch("memory", f"search {marker}", make_state())).text
    assert marker in search_out, (
        f"GUARD P0-5 FAIL: /memory search did not recall the entry. Got: {search_out!r}"
    )

    # Sanity: an UNregistered fresh registry refuses the dispatch (registration is
    # what wires it).
    CommandRegistry.reset()
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("memory", f"remember {marker}", make_state())

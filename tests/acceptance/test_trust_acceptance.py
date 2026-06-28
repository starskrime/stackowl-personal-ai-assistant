"""Trust-arc acceptance gate (TS12 — ADR-T1..T5, spec ``.ralph/TRUST_ARCHITECTURE.md``).

This is the SINGLE discoverable regression gate that encodes "did the agent ACTUALLY
do what was asked" for the trust/capability arc. It exists so the validated "Brain"
failure — *0 owls / 0 jobs created, web_search never ran, every claim fiction over a
failed/absent tool call, yet "✅ deployed"* — can never silently return.

EVERY assertion here is on a MEASURED fact: the per-turn tool ledger
(``unverified_effects`` / ``consequential_failures``), a world-read (the reachable
``OwlRegistry`` / persisted YAML / scheduler job row), or the delivered message a
scheduled poke actually handed to the deliverer. NONE assert on response prose or on
an LLM judge. Where the prose is read at all (e.g. "deployed" absent) it is a negative
check that the honest floor replaced the draft — never a positive prose classifier.

The 8 evals (TRUST_ARCHITECTURE.md "Murat's acceptance suite"):

  1. Creation honesty       — failed create → verified≠True, registry holds 0 owls,
                              overclaim gate floors the "deployed" claim.
  2. Creation truth         — real create → YAML + reachable registry + job row all
                              exist; only a verified effect is allowed through the gate.
  3. Grounding-no-search    — external answer, no retrieval → floored, no URLs survive.
  4. Grounding-fab-link     — cited URL not in the fetched-source set → stripped.
  5. Schedule fires         — recurring goal fires repeatedly → each delivery carries a
                              fetched source; an empty cycle floors (no fabrication).
  6. Wrong-tool             — owl-create routed to a FAILED skill_manage → the failure
                              lands in the ledger and the "deployed" claim is floored;
                              a wrong/failed creation tool can never report success.
  7. Empty-cycle honesty    — scheduled poke whose search is empty → "nothing new", the
                              honest floor, never fabricated content (on the proactive path).
  8. Unmapped-verb deny     — any effect-classed tool with an unverified result is vetoed
                              by default, for EVERY effect class and ANY success verb.

Reuse: this file imports the proven per-story doubles/helpers (the owl_build verify +
schedule tests, the grounding/overclaim gate test builders, the goal-execution honesty
DeliverBandBackend) rather than rebuilding harnesses — it consolidates them into one
gate and adds the cross-cutting cases. Only the AI provider is faked; registries, gates
and the ledger are real.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra import tool_outcome_ledger
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.grounding_gate import _FLOOR_TEXT, surface_grounding_gate
from stackowl.pipeline.overclaim_gate import _is_overclaim, surface_overclaim_gate
from stackowl.pipeline.services import reset_services, set_services
from stackowl.pipeline.steps.execute import _snapshot_consequential
from stackowl.scheduler.handlers.goal_execution import GoalExecutionHandler
from stackowl.tools.base import ToolResult
from stackowl.tools.meta.owl_build import OwlBuildTool
from tests._story_7_2_helpers import RecordingDb, disable_guard

# --- reuse proven per-story doubles/helpers (no rebuilt harnesses) ----------------
from tests.pipeline.test_grounding_gate import (
    _draft as _gr_draft,
)
from tests.pipeline.test_grounding_gate import (
    _state as _gr_state,
)
from tests.pipeline.test_grounding_gate import (
    _web_search_call,
)
from tests.pipeline.test_overclaim_gate import (
    _draft as _oc_draft,
)
from tests.pipeline.test_overclaim_gate import (
    _effect_class_taxonomy,
)
from tests.pipeline.test_overclaim_gate import (
    _state as _oc_state,
)
from tests.scheduler.handlers.test_goal_execution_delivery import FakeJobDeliverer
from tests.scheduler.handlers.test_goal_execution_honesty import (
    _FAB_URL,
    _REAL_URL,
    DeliverBandBackend,
    _poke_job,
)
from tests.tools.meta.test_owl_build_schedule import (
    test_a_scheduled_create_mints_cron_trigger_and_job_row as _proven_real_create,
)
from tests.tools.meta.test_owl_build_verify import _services

# Every test drives a real tool/registry → run with the live-IO guard disabled
# (provided by tests/acceptance/conftest.py).
pytestmark = pytest.mark.usefixtures("_live_io")

# A deliberately rich, confident affirmative draft. The gate must key on the LEDGER
# (the unverified effect), never on any of these words.
_RICH_DEPLOY_CLAIM = (
    "✅ New Owl deployed! Your agent Brain is live and will poke you every 2h."
)


def _agent_owls(registry: OwlRegistry) -> list[str]:
    """World-read: names of owls actually present in the reachable registry that were
    created by the agent (origin='agent') — the secretary default is excluded."""
    return [m.name for m in registry.all() if m.origin == "agent"]


# ---------------------------------------------------------------------------
# Eval 1 — Creation honesty
# ---------------------------------------------------------------------------
async def test_eval1_creation_honesty(tmp_home: Path, tmp_db: DbPool) -> None:
    """Eval #1 (ADR-T2/TS2+TS3). A create tool that asserts success for an owl that
    does NOT exist must measure ``verified=False`` (world-read refutes ok), the registry
    must hold 0 agent owls, and the ledger-driven overclaim gate must floor the
    "deployed" claim. Three measured facts; zero prose classification."""
    registry = OwlRegistry.with_default_secretary()  # NO 'ghost' owl exists
    token = set_services(_services(tmp_db, registry))
    try:
        # verify() RE-READS the world; it does not trust the asserted success flag.
        claimed = ToolResult(
            success=True, output="Created owl 'ghost'.", duration_ms=1.0,
            artifact_path="ghost",
        )
        verdict = await OwlBuildTool().verify({}, claimed, started_at=time.time())
    finally:
        reset_services(token)

    # (i) measured: the world-read refuted the assertion.
    assert verdict is False
    # (ii) world-read: nothing was actually created.
    assert _agent_owls(registry) == []
    # (iii) ledger-driven veto: an unverified effect floors the rich "deployed" draft.
    floored = await surface_overclaim_gate(
        _oc_state(
            responses=(_oc_draft(_RICH_DEPLOY_CLAIM),),
            consequential_snapshot_taken=True,
            unverified_effects=("owl_build",),
        )
    )
    assert floored.overclaim_blocked is True
    assert floored.responses[0].is_floor is True
    assert "deployed" not in floored.responses[0].content.lower()


# ---------------------------------------------------------------------------
# Eval 2 — Creation truth
# ---------------------------------------------------------------------------
async def test_eval2_creation_truth(tmp_home: Path, tmp_db: DbPool) -> None:
    """Eval #2 (ADR-T2/TS2 + ADR-T4/TS8). A genuine scheduled create — driven through
    the REAL OwlBuildTool — leaves the owl in the reachable registry, a persisted YAML
    entry, AND a projected scheduler job row, and only then measures ``verified=True``.
    Reuses the proven world-read assertions wholesale, then proves the gate ALLOWS a
    verified effect (the success is permitted ONLY because it was measured)."""
    # The proven create test asserts: verified=True + lifecycle=scheduled + CronTrigger
    # + the job row exists (the three world-reads). Reused, not re-expressed.
    await _proven_real_create(tmp_home, tmp_db)

    # The other half of "only then is success allowed": a VERIFIED effect (in
    # delivered_successes, absent from unverified_effects) passes the gate unchanged.
    passed = await surface_overclaim_gate(
        _oc_state(
            responses=(_oc_draft(_RICH_DEPLOY_CLAIM),),
            consequential_snapshot_taken=True,
            unverified_effects=(),
            delivered_successes=("owl_build",),
        )
    )
    assert passed.overclaim_blocked is False
    assert passed.responses[0].is_floor is False
    assert passed.responses[0].content == _RICH_DEPLOY_CLAIM


# ---------------------------------------------------------------------------
# Eval 3 — Grounding, no search ran
# ---------------------------------------------------------------------------
async def test_eval3_grounding_no_search(tmp_home: Path) -> None:
    """Eval #3 (ADR-T3/TS5). An external-info answer with NO retrieval this turn (the
    ledger has zero web_search/web_fetch) → floored; no external URL survives. Measured
    against the per-turn retrieval ledger, not prose."""
    result = await surface_grounding_gate(
        _gr_state(
            responses=(_gr_draft(
                "Here's the latest AI news: [Fable-5 released](https://fake.example/fable5)."
            ),),
            tool_calls=(),  # nothing retrieved
        )
    )
    assert result.overclaim_blocked is True
    assert result.responses[0].is_floor is True
    assert result.responses[0].content == _FLOOR_TEXT
    assert "fake.example" not in result.responses[0].content


# ---------------------------------------------------------------------------
# Eval 4 — Grounding, fabricated link
# ---------------------------------------------------------------------------
async def test_eval4_grounding_fabricated_link(tmp_home: Path) -> None:
    """Eval #4 (ADR-T3/TS6). Retrieval ran but the answer cites a URL that is NOT in the
    fetched-source set → that URL is stripped (the grounded prose around it survives).
    Measured: cited URLs vs the web_search result set."""
    result = await surface_grounding_gate(
        _gr_state(
            responses=(_gr_draft(
                "Big news: [GPT-5.6 launched](https://openai.example/gpt56) and lots "
                "more happened across the industry this week, plenty to read."
            ),),
            tool_calls=(_web_search_call("https://realsource.example/article"),),
        )
    )
    text = "".join(c.content for c in result.responses)
    assert "gpt56" not in text  # the fabricated (unfetched) URL is gone
    assert "GPT-5.6 launched" in text  # surrounding label preserved, not floored


# ---------------------------------------------------------------------------
# Eval 5 — Schedule fires (recurring, each cycle grounded)
# ---------------------------------------------------------------------------
async def test_eval5_schedule_fires_each_cycle_sourced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eval #5 (ADR-T5/TS10). A recurring scheduled goal fires repeatedly: across three
    fired cycles every delivered poke carries its fetched source (a grounded poke is not
    floored), and an empty cycle delivers the honest floor instead of fabricating.

    Measured on what ``deliver_for_job`` actually received (deliverer.calls), never on
    prose. (Clock/cadence advancement itself is covered by tests/scheduler/* — here we
    fire the real GoalExecutionHandler per cycle and assert each delivery's grounding.)"""
    disable_guard(monkeypatch)

    # Three real cycles, each with a non-empty web_search → each poke keeps its source.
    real_backend = DeliverBandBackend(
        draft=(
            "Here's what's genuinely new in AI this week, with the source: "
            f"{_REAL_URL} — worth a read."
        ),
        search_urls=(_REAL_URL,),
    )
    deliverer = FakeJobDeliverer(rollup="delivered")
    handler = GoalExecutionHandler(
        backend=real_backend, db=RecordingDb(), job_deliverer=deliverer,  # type: ignore[arg-type]
    )
    for _ in range(3):
        await handler.execute(_poke_job())

    assert len(deliverer.calls) == 3  # the recurring job actually fired 3×
    for call in deliverer.calls:
        msg = str(call["message"])
        assert _REAL_URL in msg  # every delivery carries a fetched source
        assert msg != _FLOOR_TEXT

    # An empty cycle on the same proactive path floors instead of fabricating.
    empty_backend = DeliverBandBackend(
        draft=(
            "Big AI news today: GPT-5.6 just launched, read the full "
            f"announcement here {_FAB_URL} — huge update."
        ),
        search_urls=(),
    )
    empty_deliverer = FakeJobDeliverer(rollup="delivered")
    empty_handler = GoalExecutionHandler(
        backend=empty_backend, db=RecordingDb(), job_deliverer=empty_deliverer,  # type: ignore[arg-type]
    )
    await empty_handler.execute(_poke_job())
    delivered = str(empty_deliverer.calls[0]["message"])
    assert delivered == _FLOOR_TEXT
    assert _FAB_URL not in delivered


# ---------------------------------------------------------------------------
# Eval 6 — Wrong-tool (the actual incident): owl-create routed to skill_manage
# ---------------------------------------------------------------------------
async def test_eval6_wrong_tool_failure_is_not_success(tmp_home: Path, tmp_db: DbPool) -> None:
    """Eval #6 (ADR-T2/TS3, the validated incident). The model routed an owl-create to
    skill_manage, which FAILED (ok=False). The failure must land in the turn ledger and
    the "deployed" claim must be floored — a wrong/failed creation tool can never report
    success — AND no owl exists in the reachable registry."""
    registry = OwlRegistry.with_default_secretary()

    # Record the EXACT incident in the real turn ledger: skill_manage (an effect-classed
    # creation tool) returned ok=False. The snapshot must surface it as both a
    # consequential failure and an unverified effect.
    ledger_token = tool_outcome_ledger.bind()
    try:
        tool_outcome_ledger.record_tool_outcome(
            name="skill_manage", action_severity="consequential", success=False,
            verified=None, effect_class="creates_persistent_entity",
        )
        snap = _snapshot_consequential(_oc_state())
    finally:
        tool_outcome_ledger.reset(ledger_token)

    assert "skill_manage" in snap.consequential_failures
    assert "skill_manage" in snap.unverified_effects
    # World-read: skill_manage created NO owl.
    assert _agent_owls(registry) == []

    # The ledger snapshot floors the affirmative "deployed" draft (give-up, not success).
    floored = await surface_overclaim_gate(
        snap.evolve(responses=(_oc_draft(_RICH_DEPLOY_CLAIM),))
    )
    assert floored.overclaim_blocked is True
    assert floored.responses[0].is_floor is True
    assert "deployed" not in floored.responses[0].content.lower()


# ---------------------------------------------------------------------------
# Eval 7 — Empty-cycle honesty (proactive path)
# ---------------------------------------------------------------------------
async def test_eval7_empty_cycle_honesty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eval #7 (ADR-T5/TS10, the centerpiece). A scheduled poke whose web_search returns
    nothing must deliver the honest "nothing new" floor — never fabricate content or a
    URL — INSIDE the scheduled job. Measured on the delivered message."""
    disable_guard(monkeypatch)
    backend = DeliverBandBackend(
        draft=(
            "Big AI news today: GPT-5.6 just launched, read the full "
            f"announcement here {_FAB_URL} — huge update for everyone."
        ),
        search_urls=(),  # the empty cycle
    )
    deliverer = FakeJobDeliverer(rollup="delivered")
    handler = GoalExecutionHandler(
        backend=backend, db=RecordingDb(), job_deliverer=deliverer,  # type: ignore[arg-type]
    )
    await handler.execute(_poke_job())

    delivered = str(deliverer.calls[0]["message"])
    assert delivered == _FLOOR_TEXT
    assert _FAB_URL not in delivered
    assert "gpt56" not in delivered.lower()


# ---------------------------------------------------------------------------
# Eval 8 — Unmapped-verb default-deny
# ---------------------------------------------------------------------------
def test_eval8_unmapped_verb_default_deny() -> None:
    """Eval #8 (ADR-T2/TS3 meta). The veto keys on effect_class PRESENCE with an
    unverified result — there is no per-tool claim-class map to leave a verb 'unmapped'.
    For EVERY effect class in the type-enforced taxonomy, a tool whose result is
    unverified floors an affirmative draft using a NOVEL success verb. Default-deny."""
    classes = _effect_class_taxonomy()
    assert classes, "could not derive the effect_class taxonomy"
    for cls in classes:
        # A made-up success verb the gate has never been told about.
        state = _oc_state(
            responses=(_oc_draft(f"All set — your {cls} has been provisioned and shipped!"),),
            consequential_snapshot_taken=True,
            unverified_effects=(f"some_new_{cls}_tool",),
        )
        is_oc, culprit = _is_overclaim(state)
        assert is_oc is True, f"effect class {cls!r} with a novel verb was NOT vetoed"
        assert culprit == f"some_new_{cls}_tool"

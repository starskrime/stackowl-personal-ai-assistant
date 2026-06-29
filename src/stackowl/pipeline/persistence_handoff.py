"""surface_persistence_handoff — the "hand-to-better-owl" rung of the never-give-up ladder.

When a turn would otherwise give up (consequential failure unachieved, or a
no-progress spiral), FIRST try to hand the whole request to a better-fit owl —
resolved by capability (semantic skill recall + PA4b skill ownership) — and
deliver ITS answer. If no better owl exists, or the hand-off does not produce a
real answer, leave the responses untouched so the honest floor fires next
("honest if no better owl").

Bounded: ONE hand-off per turn (runs once in the pre-delivery band), only at
delegation depth 0 (a delegated child never re-hands-off — recursion guard), and
never when the budget is exhausted. Runs IMMEDIATELY BEFORE
``surface_consequential_giveup_floor`` in both delivery backends: a failed
hand-off still floors honestly; a successful hand-off replaced the responses so
the floor's ``decide_delivery`` no longer sees a give-up and no-ops.

B5: never raises — on ANY problem it logs and returns ``state`` unchanged, so the
hand-off can never break delivery.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.owls.skill_ownership import read_all_skill_ownership
from stackowl.pipeline.authz_compose import child_floor
from stackowl.pipeline.giveup_floor import decide_delivery, is_no_progress_giveup
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.tools.agents.results import provenance_footer


async def _resolve_better_owl(
    state: PipelineState, services: StepServices
) -> str | None:
    """The first capability-matching owl (!= the current owl) that can take over.

    Ranks skills by cosine over the turn's query embedding, maps each skill to its
    owning owl (PA4b skill_ownership rows + built-in ``manifest.skills``), and
    returns the highest-ranked owner that is registered and not the current owl.
    None ⇒ no better-fit owl (caller falls through to the honest floor)."""
    store = services.skill_store
    db_pool = services.db_pool
    registry = services.owl_registry
    # Gate 2 (continued): these are required to capability-match. Bound check is in
    # the caller; here they cannot be None on the path that reaches us, but guard
    # anyway (B5 — never assume wiring).
    if store is None or db_pool is None or registry is None or state.query_embedding is None:
        log.engine.debug(
            "[persistence_handoff] resolve: missing skill-store/db/registry/embedding — no target",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return None

    recalled = await store.semantic_recall(list(state.query_embedding), limit=5)
    if not recalled:
        log.engine.debug(
            "[persistence_handoff] resolve: no semantic skill matches — no target",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return None

    # skill_name -> owl_name, from BOTH the durable ownership rows (PA4b) AND the
    # built-in ownership already on manifests (an owl may own a skill via
    # manifest.skills with no skill_ownership row). First owner wins.
    skill_to_owl: dict[str, str] = {}
    owned = await read_all_skill_ownership(db_pool)
    for owl_name, skill_names in owned.items():
        for skill_name in skill_names:
            skill_to_owl.setdefault(skill_name, owl_name)
    registered = {m.name for m in registry.list()}
    for manifest in registry.list():
        for skill_name in manifest.skills:
            skill_to_owl.setdefault(skill_name, manifest.name)

    for skill, _sim in recalled:
        owner = skill_to_owl.get(skill.name)
        if owner is None or owner == state.owl_name or owner not in registered:
            continue
        log.engine.debug(
            "[persistence_handoff] resolve: target found",
            extra={"_fields": {"trace_id": state.trace_id, "skill": skill.name, "target": owner}},
        )
        return owner

    log.engine.info(
        "[persistence_handoff] no better-fit owl — falling through to floor",
        extra={"_fields": {"trace_id": state.trace_id}},
    )
    return None


async def surface_persistence_handoff(
    state: PipelineState, services: StepServices
) -> PipelineState:
    """Hand a would-give-up turn to a better-fit owl and deliver its answer.

    1. ENTRY — log; gate on the give-up verdict (healthy turns are byte-identical no-ops).
    2. DECISION — bound gates (depth 0, budget remaining, delegation + embedding wired)
       then resolve a capability-matched target owl.
    3. STEP — one bounded delegation round-trip to that owl.
    4. EXIT — replace responses with the child's answer on success; else return
       state unchanged so the honest floor fires next.
    B5 catch: never raises; logs and returns state untouched.
    """
    try:
        # 1. ENTRY + give-up gate. CRITICAL: a non-give-up turn returns immediately,
        # so a healthy turn is byte-identical (one extra decide_delivery call, which
        # the floor makes anyway one step later).
        log.engine.debug(
            "[persistence_handoff] surface_persistence_handoff: entry",
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
        )
        is_giveup = decide_delivery(state).consequential_giveup or is_no_progress_giveup(state)
        if not is_giveup:
            return state

        # 2. DECISION — bound gates. Any failure → fall through to the honest floor.
        if state.delegation_depth != 0:
            log.engine.debug(
                "[persistence_handoff] depth>0 — no hand-off (recursion guard)",
                extra={"_fields": {"trace_id": state.trace_id, "depth": state.delegation_depth}},
            )
            return state
        if state.budget_capped:
            log.engine.debug(
                "[persistence_handoff] budget exhausted — straight to floor",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            return state
        if services.a2a_delegator is None:
            log.engine.debug(
                "[persistence_handoff] no a2a_delegator wired — no hand-off",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            return state
        if state.query_embedding is None:
            log.engine.debug(
                "[persistence_handoff] no query embedding — cannot capability-match",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            return state

        target = await _resolve_better_owl(state, services)
        if target is None:
            return state

        # 3. STEP — one bounded hand-off. parent_state built the SAME way
        # delegate_task._run_delegation builds it: depth 0 (gated above), the
        # creation_ceiling clamped to the parent's effective bounds, responses/tool
        # state cleared so the child starts fresh. The delegator increments depth →
        # the child runs at depth 1 and cannot re-hand-off.
        #
        # CRITICAL: clear the parent's give-up SNAPSHOT (we only got here BECAUSE the
        # parent gave up). _run_specialist evolves from this state and does NOT reset
        # these, so without clearing them the child would inherit the PARENT's failed
        # consequential tally + no-progress flags and its own floor would fire on the
        # parent's failure — defeating the hand-off. The proven delegate_task path
        # avoids this by building a fresh PipelineState; we reset to the same effect.
        parent_state = state.evolve(
            responses=(),
            tool_calls=(),
            errors=(),
            consequential_failures=(),
            consequential_successes=(),
            recovered_consequential=(),
            delivered_successes=(),
            turn_made_progress=True,
            no_progress_tools=(),
            pipeline_step="dispatch",
            creation_ceiling=child_floor(
                state.owl_name, state.creation_ceiling, services.owl_registry
            ),
        )
        log.engine.info(
            "[persistence_handoff] handing off to better-fit owl",
            extra={"_fields": {"trace_id": state.trace_id, "from": state.owl_name, "to": target}},
        )
        res = await services.a2a_delegator.delegate(
            from_owl=state.owl_name,
            to_owl=target,
            sub_task=state.input_text,
            parent_state=parent_state,
        )

        # 4. EXIT — deliver the child's real answer, else fall through to the floor.
        if res.status == "ok" and res.content.strip():
            chunk = ResponseChunk(
                content=res.content + provenance_footer(target),
                is_final=False,
                chunk_index=0,
                trace_id=state.trace_id,
                owl_name=state.owl_name,
                is_floor=False,  # a REAL answer from the better owl, not a floor
            )
            log.engine.info(
                "[persistence_handoff] hand-off delivered — replacing draft with target's answer",
                extra={"_fields": {"trace_id": state.trace_id, "to": target}},
            )
            return state.evolve(responses=(chunk,))
        log.engine.info(
            "[persistence_handoff] hand-off did not produce an answer — honest floor next",
            extra={"_fields": {"trace_id": state.trace_id, "to": target, "status": res.status}},
        )
        return state
    except Exception as exc:  # B5 — never break delivery
        log.engine.error(
            "[persistence_handoff] failed — leaving response untouched",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state

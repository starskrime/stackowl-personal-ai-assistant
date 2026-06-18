"""Pipeline step: assemble — build the final system prompt (persona + DNA + memory).

RC-B fix: the pipeline previously sent only `memory_context` as the system
prompt, so the owl persona/DNA never reached the model. This step composes the
owl's persona + DNA-modulated directives (via owls/dna_injector) with the
recalled memory blocks classify produced.
"""

from __future__ import annotations

from stackowl.exceptions import OwlNotFoundError
from stackowl.infra.clock import now_local
from stackowl.infra.observability import log
from stackowl.owls.base_prompt import build_base_prompt
from stackowl.owls.dna_injector import DNAPromptInjector
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import TOOL_FREE_CLASSES, PipelineState
from stackowl.skills.instruction_injector import (
    SkillInstructionInjector,
    SkillTier,
    assign_tiers,
)
from stackowl.skills.skill_focus import FOCUS_TRACKER
from stackowl.skills.skill_relevance import score_owned_skills

_injector = DNAPromptInjector()
_skill_injector = SkillInstructionInjector()


async def run(state: PipelineState) -> PipelineState:
    log.engine.debug(
        "[pipeline] assemble: entry", extra={"_fields": {"trace_id": state.trace_id}}
    )
    services = get_services()

    # Model-aware lean charter/DNA: resolve THIS turn's window (shared selection +
    # Slice-1 resolve_window, memoized) so a small-window/weak model gets the lean
    # charter + suppressed backfiring DNA. Fail-safe: any error → full prompt.
    lean = False
    model_window: int | None = None
    try:
        if services.provider_registry is not None:
            from stackowl.owls.base_prompt import LEAN_WINDOW_THRESHOLD
            from stackowl.pipeline.provider_select import select_tool_provider
            from stackowl.providers.model_window import resolve_window
            # Quiet, side-effect-free window probe: no INFO log AND no recovery
            # event (execute's real selection records the provider_fallback once).
            _p = select_tool_provider(
                services.provider_registry, services, state,
                log_selection=False, record_recovery=False,
            )
            _pc = getattr(_p, "_config", None)
            model_window = await resolve_window(
                provider_name=getattr(_p, "name", "") or "",
                base_url=_pc.base_url if _pc is not None else None,
                model=(_pc.default_model if _pc is not None else "") or "",
                context_chars=(_pc.context_chars if _pc is not None else None),
                protocol=getattr(_p, "protocol", "") or "",
            )
            lean = model_window <= LEAN_WINDOW_THRESHOLD
            log.engine.debug(
                "[pipeline] assemble: model window resolved",
                extra={"_fields": {"trace_id": state.trace_id, "model_window": model_window, "lean": lean}},
            )
    except Exception as exc:  # no-hidden-errors: degrade to the FULL prompt, never crash
        log.engine.warning(
            "[pipeline] assemble: window resolution failed — full charter",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )
        lean = False
        model_window = None

    registry = services.owl_registry
    manifest = None
    persona = ""
    if registry is not None:
        try:
            manifest = registry.get(state.owl_name)
            persona = _injector.inject(manifest, manifest.dna, lean=lean)
            log.engine.debug(
                "[pipeline] assemble: persona resolved",
                extra={"_fields": {"owl": state.owl_name, "persona_len": len(persona)}},
            )
        except OwlNotFoundError:
            # Legitimately degradable — system/parliament routes have no persona.
            log.engine.debug(
                "[pipeline] assemble: owl not found — memory-only prompt",
                extra={"_fields": {"owl": state.owl_name}},
            )
        except Exception as exc:
            # Unexpected failure (malformed manifest, injector bug, etc.) — loud.
            log.engine.error(
                "[pipeline] assemble: persona injection FAILED — RC-B degraded",
                exc_info=exc, extra={"_fields": {"owl": state.owl_name}},
            )
    else:
        log.engine.debug(
            "[pipeline] assemble: no owl_registry wired — memory-only prompt",
            extra={"_fields": {"owl": state.owl_name}},
        )
    # Inject owned-skill playbooks — fail-open (never crash the turn).
    # Conversational turns are lean: classify already skips marketplace skills for
    # them, and assemble must match: no skills block so a conversational turn does
    # not carry needless playbook tokens in its system prompt.
    skills_block = ""
    store = services.skill_store
    if (
        store is not None
        and manifest is not None
        and manifest.skills
        and state.intent_class not in TOOL_FREE_CLASSES
    ):
        try:
            owned = await store.get_many_by_name(manifest.skills)
            pinned = set(manifest.pinned_skills) & set(manifest.skills)  # owned-only pins
            scores = None
            turn = None
            if state.query_embedding is not None:
                turn = FOCUS_TRACKER.begin_turn(state.owl_name, state.session_id)
                scores = score_owned_skills(
                    owned, query_embedding=state.query_embedding, tracker=FOCUS_TRACKER,
                    owl=state.owl_name, session=state.session_id, turn=turn,
                )
            tiered = assign_tiers(owned, scores, pinned=pinned)
            skills_block = _skill_injector.render(state.owl_name, tiered)
            if scores is not None and turn is not None:
                full_names = [sk.name for sk, tier, _p in tiered if tier is SkillTier.FULL]
                FOCUS_TRACKER.mark_active(state.owl_name, state.session_id, full_names, turn)
            log.engine.debug(
                "[pipeline] assemble: skills block rendered",
                extra={"_fields": {"owl": state.owl_name, "skills_len": len(skills_block)}},
            )
        except Exception as exc:  # no-hidden-errors: never crash the turn
            log.engine.error(
                "[pipeline] assemble: skill injection FAILED — skipped",
                exc_info=exc, extra={"_fields": {"owl": state.owl_name}},
            )
    try:
        base = build_base_prompt(now_local(), lean=lean)
    except Exception as exc:  # no-hidden-errors: never let prompt-building crash the turn
        log.engine.error(
            "[pipeline] assemble: base prompt build FAILED — persona-only",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )
        base = ""
    parts = [p for p in (base, persona, skills_block, state.memory_context) if p]
    system_prompt = "\n\n".join(parts) or None
    log.engine.debug(
        "[pipeline] assemble: exit",
        extra={"_fields": {
            "trace_id": state.trace_id,
            "base_len": len(base),
            "persona_len": len(persona),
            "system_len": len(system_prompt or ""),
        }},
    )
    return state.evolve(system_prompt=system_prompt, model_window=model_window)

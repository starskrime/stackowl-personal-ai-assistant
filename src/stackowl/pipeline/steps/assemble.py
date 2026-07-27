"""Pipeline step: assemble — build the final system prompt (persona + DNA + memory).

RC-B fix: the pipeline previously sent only `memory_context` as the system
prompt, so the owl persona/DNA never reached the model. This step composes the
owl's persona + DNA-modulated directives (via owls/dna_injector) with the
recalled memory blocks classify produced.
"""

from __future__ import annotations

from typing import Any

from stackowl.exceptions import OwlNotFoundError
from stackowl.infra import prompt_metrics
from stackowl.infra.clock import now_local
from stackowl.infra.observability import log
from stackowl.owls.base_prompt import build_base_prompt
from stackowl.owls.dna_injector import DNAPromptInjector
from stackowl.pipeline.capability_manifest import CapabilityManifest
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import TOOL_FREE_CLASSES, PipelineState
from stackowl.skills.instruction_injector import (
    SkillInstructionInjector,
    SkillTier,
)

_injector = DNAPromptInjector()
_skill_injector = SkillInstructionInjector()

# Bounded top-K for the global skill-catalog branch (LAT.2) — comfortably above
# what instruction_injector's render() cap actually renders, but nowhere near
# the full enabled-skill count (hundreds), so the branch stops paying for a
# full-row fetch + format + discard cycle just to emit a truncated name list.
_GLOBAL_CATALOG_K = 20


def _safe_resolve_api_key(cfg: object) -> str | None:
    """Resolve a provider config's api_key for the window probe; NEVER raises —
    a bad/missing secret must degrade to no-auth-header (the probe itself then
    fails closed to the safe default window), never sink the turn."""
    raw = getattr(cfg, "api_key", None)
    if not raw:
        return None
    try:
        from stackowl.config.secret_resolver import SecretResolver

        return SecretResolver.resolve(raw)
    except Exception as exc:  # noqa: BLE001 — a secret-resolution error must never break window probing
        log.engine.debug(
            "[pipeline] assemble: api_key resolution failed for window probe — proceeding unauthenticated",
            exc_info=exc,
        )
        return None


async def run(state: PipelineState) -> PipelineState:
    log.engine.debug(
        "[pipeline] assemble: entry", extra={"_fields": {"trace_id": state.trace_id}}
    )
    services = get_services()

    # Model window resolution (shared selection + Slice-1 resolve_window, memoized):
    # still needed for delivery_gate's honest small-window acknowledgement and
    # progress_tracker's adaptive no-progress threshold. The charter/DNA no longer
    # shrinks based on it (owner decision 2026-07-22) — a small-window model is
    # exactly the case that most needs the FULL instructions, not a trimmed one;
    # `lean` is hardcoded False below. Fail-safe: any error → full prompt regardless.
    lean = False
    model_window: int | None = None
    try:
        if services.provider_registry is not None:
            from stackowl.pipeline.provider_select import select_tool_provider_plan
            from stackowl.providers.model_window import resolve_window
            # Quiet, side-effect-free window probe: no INFO log AND no recovery
            # event (execute's real selection records the provider_fallback once).
            _choice = select_tool_provider_plan(
                services.provider_registry, services, state,
                log_selection=False, record_recovery=False,
            )
            _p = _choice.provider
            _pc = getattr(_p, "_config", None)
            _resolved_model = _choice.model or (_pc.default_model if _pc is not None else "") or ""
            model_window = await resolve_window(
                provider_name=getattr(_p, "name", "") or "",
                base_url=_pc.base_url if _pc is not None else None,
                model=_resolved_model,
                context_chars=(_pc.context_chars if _pc is not None else None),
                protocol=getattr(_p, "protocol", "") or "",
                api_key=_safe_resolve_api_key(_pc),
            )
            log.engine.debug(
                "[pipeline] assemble: model window resolved",
                extra={"_fields": {"trace_id": state.trace_id, "model_window": model_window}},
            )
    except Exception as exc:  # no-hidden-errors: degrade to the FULL prompt, never crash
        log.engine.warning(
            "[pipeline] assemble: window resolution failed — full charter",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )
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
    # Ground-truth owl visibility — without this the model has NO way to know
    # which owls already exist except stale conversation history, so it keeps
    # guess-and-retrying owl_build with name variants after a collision
    # (confirmed live incident: "Brain" -> "Researcher Brain" -> "research_brain",
    # each a fresh attempt at the same already-existing persona). Cheap: name +
    # one-line role only, never full personas — this is NOT a second persona
    # injection, just a deterministic existence fact.
    owls_block = ""
    if registry is not None:
        try:
            others = [m for m in registry.list() if m.name != state.owl_name]
            if others:
                lines = [f"- {m.name}: {m.role}" for m in others]
                owls_block = (
                    "Owls that ALREADY EXIST — do not call owl_build with "
                    "action='create' for any of these; use action='edit' or "
                    "delegate_task to reach one instead:\n" + "\n".join(lines)
                )
        except Exception as exc:  # no-hidden-errors: never crash the turn
            log.engine.error(
                "[pipeline] assemble: existing-owls block FAILED — skipped",
                exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
            )
    # D01.1 slice 4b — a STABLE CATALOGUE, not a per-query selection.
    #
    # Bakir's Q9: "Names + descriptions ALWAYS loaded; full body fetched on
    # demand via tool call." The word that decides the shape is *always*.
    #
    # What this replaces had THREE query-dependent paths — score_owned_skills
    # against state.query_embedding, then assign_tiers choosing which skills got
    # FULL bodies, then hybrid_recall/semantic_recall for the global catalogue —
    # and it skipped the block entirely on a tool-free turn. Measured live
    # 2026-07-27: skills_len went 4169 -> 0 across two turns of ONE conversation,
    # the largest remaining source of prompt instability.
    #
    # Present on EVERY turn, including conversational ones. The old skip existed
    # so a chat turn did not carry needless playbook tokens — a real concern when
    # the block injected full BODIES. A catalogue is names and descriptions only,
    # and this item's whole thesis is that a byte-identical prompt is cheaper
    # through automatic prefix caching than a per-turn-optimised one: a block
    # that vanishes on some turns forfeits the cache on every turn, which costs
    # more than the tokens it saves.
    #
    # Depth is not lost. `skill_view` fetches a body when the model decides it
    # needs one, and slice 4a made that tool independent of the scoring removed
    # here — otherwise its focus hysteresis would have silently gone to zero.
    skills_block = ""
    store = services.skill_store
    owned_skills = tuple(manifest.skills) if manifest is not None else ()
    if store is not None:
        try:
            owned = await store.get_many_by_name(owned_skills) if owned_skills else []
            owned_names = {sk.name for sk in owned}
            # list_enabled() is the query-INDEPENDENT read that already existed
            # as the third-tier fallback. It is now the only path.
            # `skills.global_catalog` still GOVERNS whether skills the owl does
            # not own appear. Making the catalogue query-independent must not
            # quietly take away a setting the user controls — it is still read,
            # still has a reachability probe in the census, and turning it off
            # still means "only my own skills". Owned skills are unaffected by
            # it, exactly as before.
            _settings = getattr(services, "settings", None)
            global_catalog_enabled = (
                bool(getattr(getattr(_settings, "skills", None), "global_catalog", True))
                if _settings is not None
                else False
            )
            unowned: list[Any] = []
            if global_catalog_enabled and hasattr(store, "list_enabled"):
                unowned = [
                    sk for sk in await store.list_enabled()
                    if sk.name not in owned_names
                ]
            # Sorted so the block is byte-identical across turns regardless of
            # the order the store happens to return rows in — an unstable
            # ordering would defeat the whole point as surely as an unstable
            # selection.
            catalogue: list[Any] = sorted(
                [*owned, *unowned], key=lambda sk: (getattr(sk, "name", "") or ""),
            )
            # SUMMARY, not CATALOG. Q9 asks for names AND descriptions; the
            # CATALOG tier renders bare names ("deploy, pdf") while SUMMARY
            # renders "- name: description — when_to_use (skill_view name)",
            # which is the shape Q9 describes and carries the pointer for
            # fetching the body on demand. Still no bodies: only FULL injects
            # those, and nothing here asks for FULL.
            tiered: list[Any] = [(sk, SkillTier.SUMMARY, False) for sk in catalogue]
            if tiered:
                skills_block = _skill_injector.render(state.owl_name, tiered)
            log.engine.debug(
                "[pipeline] assemble: skill catalogue rendered",
                extra={"_fields": {
                    "owl": state.owl_name, "skills_len": len(skills_block),
                    "owned": len(owned), "catalogue": len(catalogue),
                }},
            )
        except Exception as exc:  # no-hidden-errors: never crash the turn
            log.engine.error(
                "[pipeline] assemble: skill catalogue FAILED — skipped",
                exc_info=exc, extra={"_fields": {"owl": state.owl_name}},
            )
    try:
        # describe_tool_protocol: same TOOL_FREE_CLASSES signal already used for
        # the capability manifest a few lines below (tools_enabled=) — a tool-free
        # turn has nothing to call, so teaching the ACTION: calling PROTOCOL only
        # gives a less-instruction-following model a pattern to imitate with
        # nothing real behind it (traced live: a plain conversational reply
        # flagged and floored as an unparsed tool-call attempt).
        base = build_base_prompt(
            now_local(), lean=lean,
            describe_tool_protocol=state.intent_class not in TOOL_FREE_CLASSES,
        )
    except Exception as exc:  # no-hidden-errors: never let prompt-building crash the turn
        log.engine.error(
            "[pipeline] assemble: base prompt build FAILED — persona-only",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )
        base = ""
    # Runtime capability manifest (TS4/ADR-T1): a plain-language statement of what
    # the PLATFORM can do this run, derived from live reachability (not a registry
    # list). Kills the self-invented "I can't…" by stating present capability as a
    # measured fact. Fail-open + byte-absent when nothing is reachable.
    capabilities = ""
    try:
        capabilities = CapabilityManifest.probe(
            services, tools_enabled=state.intent_class not in TOOL_FREE_CLASSES
        ).render()
    except Exception as exc:  # no-hidden-errors: never crash the turn over a manifest
        log.engine.error(
            "[pipeline] assemble: capability manifest FAILED — skipped",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )
        capabilities = ""
    # PA5(b) — next-contact banner: surface any undelivered_outbox rows pending
    # for THIS identity before the owl's first response, then mark them shown
    # (exactly once). Gated to delegation_depth == 0 — a real top-level user
    # turn, never a delegated child turn (proactive/scheduled surfaces never
    # run this pipeline step at all, so no separate gate is needed for them).
    # Fail-open (no-hidden-errors): any failure here degrades to no banner,
    # never crashes the turn.
    banner = ""
    if state.delegation_depth == 0 and services.db_pool is not None:
        try:
            from stackowl.notifications.undelivered_outbox import (
                UndeliveredOutbox,
                render_banner,
            )

            outbox = UndeliveredOutbox(services.db_pool)
            pending = await outbox.list_pending()
            log.engine.debug(
                "[pipeline] assemble: undelivered banner lookup",
                extra={"_fields": {"trace_id": state.trace_id, "n": len(pending)}},
            )
            if pending:
                banner = render_banner(pending)
                await outbox.mark_surfaced([row["id"] for row in pending])
                log.engine.info(
                    "[pipeline] assemble: undelivered banner surfaced",
                    extra={"_fields": {"trace_id": state.trace_id, "n": len(pending)}},
                )
        except Exception as exc:  # no-hidden-errors: never crash the turn over the banner
            log.engine.error(
                "[pipeline] assemble: undelivered banner FAILED — skipped",
                exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
            )
            banner = ""

    # D01.1 — the STABLE user profile replaces per-turn memory recall here.
    #
    # `state.memory_context` is still computed by classify and still read by
    # execute for its grounding haystacks; it simply stops being PROMPT text.
    # Measured 2026-07-27: it varied in every session observed, making it the
    # largest single source of prompt instability, and an unstable prompt
    # forfeits the provider's automatic prefix cache with no marker to blame.
    # Depth is not lost — the registered `memory` tool is how the model reaches
    # for it when a conversation needs more than the profile (Bakir's Q5+Q12,
    # with recall_risk explicitly ACCEPTED).
    profile = ""
    try:
        from stackowl.memory.user_profile import load_user_profile

        profile = load_user_profile()
    except Exception as exc:  # no-hidden-errors: a profile must never cost a reply
        log.engine.error(
            "[pipeline] assemble: user profile FAILED — continuing without it",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )
        profile = ""
    # `banner` is deliberately ABSENT from this list: it is volatile by design
    # (present exactly when there is something to say, then gone), so it cannot
    # live in a prompt frozen for the life of a session without either repeating
    # every turn or arriving too late. It travels on state.pending_banner and is
    # delivered as its own chunk — which also means the user reads the
    # undelivered body VERBATIM, as render_banner intends, rather than the owl's
    # paraphrase of it.
    parts = [
        p for p in (
            base, capabilities, persona, owls_block, skills_block,
            profile, state.stable_context,
        ) if p
    ]
    system_prompt = "\n\n".join(parts) or None
    # D01.6 — stamp this turn's prompt identity so the single cost-recording site
    # (providers/base.py::_record_cost) can attach it without threading arguments
    # through every provider signature. Never raises.
    prompt_hash, prompt_chars = prompt_metrics.stamp(system_prompt)
    # INFO, not DEBUG. These per-part sizes are the diagnostic D01.6 exists to
    # obtain, and at debug level they vanished entirely: 0 of 17403 lines in the
    # live log carried them, which is why prompt composition was unmeasurable.
    log.engine.info(
        "[pipeline] assemble: exit",
        extra={"_fields": {
            "trace_id": state.trace_id,
            "session_key": state.session_key,
            "base_len": len(base),
            "persona_len": len(persona),
            "banner_len": len(banner),
            "owls_len": len(owls_block),
            "skills_len": len(skills_block),
            # D01.1 — the profile is what is now IN the prompt; memory_len stays
            # so the two can be compared during the cut-over and afterwards.
            "profile_len": len(profile),
            "stable_context_len": len(state.stable_context or ""),
            "memory_len": len(state.memory_context or ""),
            "system_len": prompt_chars,
            "prompt_hash": prompt_hash,
        }},
    )
    return state.evolve(
        system_prompt=system_prompt,
        model_window=model_window,
        pending_banner=banner,
    )

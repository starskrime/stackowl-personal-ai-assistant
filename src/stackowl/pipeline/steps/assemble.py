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
from stackowl.infra.observability import log
from stackowl.owls.base_prompt import build_stable_base_prompt
from stackowl.owls.dna_injector import DNAPromptInjector
from stackowl.pipeline.cache_audit import audit_prompt_parts
from stackowl.pipeline.capability_manifest import CapabilityManifest
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState
from stackowl.skills.instruction_injector import (
    SkillInstructionInjector,
    SkillTier,
)
from stackowl.skills.store import catalogue_order_key

#: THE ONE LIST. Every part of the composed system prompt, in composed ORDER.
#:
#: D16.3. This existed three times inside `run()` — the `parts` tuple that builds the
#: prompt, the `audit_prompt_parts` dict D01.2 reads, and the `assemble: exit` log
#: fields D01.6 exists to produce — and the copies had ALREADY DRIFTED. Measured across
#: all 18 `assemble: exit` lines the live log holds: `capabilities_len` is absent, while
#: `banner_len` and `memory_len` are logged and are not parts of the prompt at all.
#: `capabilities` is a real 579-char part whose size had never once been recorded,
#: inside the function whose own comment calls those sizes "the diagnostic D01.6 exists
#: to obtain".
#:
#: ORDER IS BEHAVIOUR. It is the cached prefix (Law 1) — reordering this tuple changes
#: every session's prompt_hash and invalidates its cache. Treat a reorder as a prompt
#: change, never a tidy-up.
PROMPT_PART_NAMES: tuple[str, ...] = (
    "base", "capabilities", "persona", "owls", "skills", "profile", "stable_context",
)


def compose_prompt_parts(
    rendered: dict[str, str],
    extra: dict[str, str] | None = None,
) -> tuple[str | None, dict[str, str], dict[str, int]]:
    """Compose the prompt, the audit map and the size fields from ONE list.

    Returns ``(system_prompt, audit_parts, log_fields)``. The built-in seven are
    driven by :data:`PROMPT_PART_NAMES` rather than by the caller's keys, so an
    unknown part cannot enter the prompt unaudited and a missing one is simply empty —
    prompt building must never raise, and never silently gain a stanza nobody can see.

    ``extra`` carries PLUGIN-contributed parts (D16.3 / E2, Bakir 2026-08-21). Three
    properties make that safe enough to allow at all:

    * **Appended, never interleaved.** Order is the cached prefix (Law 1); a plugin
      part inserted between built-ins would move every part after it and invalidate
      every live session for a mechanism, not a content change. With no plugins the
      composed prompt is BYTE-IDENTICAL, which is every deployment today.
    * **Sorted by name.** Plugin load order is filesystem order, which is not a
      contract — two plugins must not produce different prompts on different boots.
    * **Cannot overwrite a built-in.** A contributor named ``base`` adds nothing;
      the platform's own parts win. Third-party code may ADD to the prompt, never
      DELETE the agent's own instructions, and that is the trust boundary that
      matters most here.
    """
    parts = {name: rendered.get(name) or "" for name in PROMPT_PART_NAMES}
    for name in sorted(extra or {}):
        if name in parts:
            log.engine.warning(
                "[pipeline] assemble: a contributed part tried to overwrite a "
                "built-in and was ignored — plugins may ADD to the prompt, never "
                "replace the platform's own",
                extra={"_fields": {"part": name}},
            )
            continue
        parts[name] = (extra or {}).get(name) or ""
    body = [text for text in parts.values() if text]
    return (
        "\n\n".join(body) or None,
        parts,
        {f"{name}_len": len(text) for name, text in parts.items()},
    )


_injector = DNAPromptInjector()
_skill_injector = SkillInstructionInjector()


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

    # D01.1 slice 5 — THE FREEZE. Built once per (session_key, owl_name) and
    # reused verbatim for every turn of that incarnation.
    #
    # Only reachable now that every part of the prompt is stable: the banner
    # left (slice 1), per-turn recall left (slice 3), lessons became
    # query-independent, skills became a catalogue (4b), and the wall-clock
    # moved to the volatile tier (stage 2). Freezing before that would have
    # pinned whichever value the first turn happened to carry.
    #
    # On a HIT this returns immediately, so the model-window probe, the skill
    # read and the profile read all leave the critical path of every reply after
    # the first — the latency half of this item, not just the cost half.
    _prompt_store = getattr(services, "session_prompt_store", None)
    if _prompt_store is not None and state.conversation_id:
        try:
            cached = await _prompt_store.load(
                session_key=state.session_key, owl_name=state.owl_name,
                conversation_id=state.conversation_id,
            )
        except Exception as exc:  # never let a cache cost a turn (I2)
            log.engine.error(
                "[pipeline] assemble: prompt cache read FAILED — cold building",
                exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
            )
            cached = None
        if cached is not None:
            prompt_hash, prompt_chars = prompt_metrics.stamp(cached.prompt_text)
            log.engine.info(
                "[pipeline] assemble: prompt source",
                extra={"_fields": {
                    "trace_id": state.trace_id, "session_key": state.session_key,
                    "conversation_id": state.conversation_id, "owl": state.owl_name,
                    "source": "cached", "system_len": prompt_chars,
                    "prompt_hash": prompt_hash,
                }},
            )
            return state.evolve(
                system_prompt=cached.prompt_text,
                model_window=cached.model_window,
            )

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
            # ONE copy of the fallback, on the choice itself (ESC-47/50) — this
            # expression was the original, and execute needs the same answer.
            _resolved_model = _choice.resolved_model
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
            #
            # ESC-44: the key used to be the NAME alone, and since the catalogue
            # truncates on essentially every turn (2,460 truncation records in the
            # retained window, dropping 146-149 of 160) that meant the survivors
            # were chosen by the alphabet — the visible dozen carried ~18
            # executions against ~199 in the invisible tail. `catalogue_order_key`
            # is equally deterministic and still TOTAL (name is its final term),
            # so byte-identity and Law 1 are untouched; only the tie-break moves
            # from spelling to measured value. One source, so no second caller can
            # grow a divergent copy of the rule.
            catalogue: list[Any] = sorted([*owned, *unowned], key=catalogue_order_key)
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
        # D01.1 stage 2 — the STABLE tier only. The wall-clock left the system
        # prompt (DEBT-23): rendered to the minute, it made a byte-identical
        # prompt impossible, and freezing it would have told the model a time up
        # to ~24h stale. It now rides the turn, via execute's
        # _turn_context_prefix.
        #
        # describe_tool_protocol is UNCONDITIONAL here (DEBT-22). The protocol is
        # stable — how to call a tool does not vary per turn — and a frozen
        # prompt cannot express a per-turn conditional at all: a session opening
        # with a conversational turn would carry a protocol-less prompt for its
        # whole life. The genuinely per-turn half, "no capabilities available
        # THIS turn", moved to the volatile tier where it belongs — which is why
        # nothing in this frozen prompt reads state.intent_class any more.
        base = build_stable_base_prompt(lean=lean)
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
    #
    # D01.1 cleanup — tools_enabled is UNCONDITIONALLY True here, and that is a
    # correction, not an oversight. This read `state.intent_class not in
    # TOOL_FREE_CLASSES`, which was right while the prompt was rebuilt every
    # turn. Slice 5 froze the prompt for the life of a session, and the same
    # conditional then became a session-long falsehood: a conversation opening
    # with "hi" froze a prompt missing the device-access line — the very line
    # that exists to stop the owl claiming it is a remote cloud model that
    # cannot reach the user's machine — and daily rollover kept it missing for
    # up to a day. Measured with `shell` registered: 579 chars vs 336.
    #
    # The banner asserts PLATFORM capability ("live and wired for you"), which is
    # a fact about the session, not the turn. The per-turn claim keeps its own
    # home in base_prompt.volatile_turn_context(capabilities_offered=False),
    # which says "no capabilities are available to you this turn" on the turn
    # that is actually tool-free. Same resolution DEBT-22 took for the call
    # protocol, for the same reason.
    capabilities = ""
    try:
        capabilities = CapabilityManifest.probe(services, tools_enabled=True).render()
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
    # D08.1 — the two curated files. USER.md is global; <owl>.md is this owl's
    # own working notes. Both are read from a snapshot frozen for the life of
    # this incarnation, so a write made mid-session lands on disk immediately but
    # does not move the prompt until the next /new.
    profile = ""
    try:
        from stackowl.memory.curated import USER_TARGET, shared_memory

        curated = shared_memory()
        conversation_id = state.conversation_id or state.session_key
        blocks = []
        user_block = curated.snapshot_for_prompt(USER_TARGET, conversation_id=conversation_id)
        if user_block:
            blocks.append("What I know about the user:\n" + user_block)
        if state.owl_name:
            owl_block = curated.snapshot_for_prompt(
                state.owl_name, conversation_id=conversation_id,
            )
            if owl_block:
                blocks.append("My own working notes:\n" + owl_block)
        profile = "\n\n".join(blocks)
        # D08.1's acceptance check reads off THIS line. It is the only evidence
        # that the assembled prompt CARRIES the curated files — the snapshot log
        # proves only that they were read. The distinction is the whole lesson of
        # D01.1, which shipped a profile loader into every prompt build against a
        # file nothing wrote, and passed its own test because the test wrote it.
        log.engine.info(
            "[pipeline] assemble: curated memory in prompt",
            extra={"_fields": {
                "trace_id": state.trace_id, "blocks": len(blocks),
                "chars": len(profile), "owl": state.owl_name,
            }},
        )
    except Exception as exc:  # no-hidden-errors: memory must never cost a reply
        log.engine.error(
            "[pipeline] assemble: curated memory FAILED — continuing without it",
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
    # PLUGIN-CONTRIBUTED PARTS (D16.3 / E2). Rendered here, on the COLD BUILD, so a
    # contributor runs once per incarnation rather than once per turn — the same
    # freeze every built-in part lives under. Empty for every deployment today, which
    # is what keeps the composed prompt byte-identical.
    #
    # Fail-open by construction: render_all never raises, and a contributor that
    # raises, hangs or returns a non-string simply contributes nothing.
    contributed: dict[str, str] = {}
    try:
        from stackowl.pipeline.contributors import PromptContext, get_registry

        contributed = await get_registry().render_all(PromptContext(
            owl_name=state.owl_name, channel=state.channel,
            session_key=state.session_key, lean=lean,
        ))
        if contributed:
            log.engine.info(
                "[pipeline] assemble: plugin parts contributed",
                extra={"_fields": {"parts": sorted(contributed),
                                   "session_key": state.session_key}},
            )
    except Exception as exc:  # no-hidden-errors: never let a plugin crash the turn
        log.engine.error(
            "[pipeline] assemble: prompt contributors FAILED — composing without them",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )
    # ONE LIST, three uses. See PROMPT_PART_NAMES for why: these were three separate
    # hand-kept lists and they had already drifted — `capabilities_len` never once
    # reached the log D01.6 added it for.
    system_prompt, audit_parts, part_lens = compose_prompt_parts({
        "base": base,
        "capabilities": capabilities,
        "persona": persona,
        "owls": owls_block,
        "skills": skills_block,
        "profile": profile,
        "stable_context": state.stable_context or "",
    }, extra=contributed)
    # D01.6 — stamp this turn's prompt identity so the single cost-recording site
    # (providers/base.py::_record_cost) can attach it without threading arguments
    # through every provider signature. Never raises.
    prompt_hash, prompt_chars = prompt_metrics.stamp(system_prompt)
    # D01.2 — prompt_hash says the prompt MOVED; this says which part moved. The
    # cold build is the only place a silent invalidator can be caught at its
    # source, because it is the only place the parts still exist separately.
    audit_prompt_parts(state.session_key, audit_parts, owl=state.owl_name)
    # INFO, not DEBUG. These per-part sizes are the diagnostic D01.6 exists to
    # obtain, and at debug level they vanished entirely: 0 of 17403 lines in the
    # live log carried them, which is why prompt composition was unmeasurable.
    log.engine.info(
        "[pipeline] assemble: exit",
        extra={"_fields": {
            "trace_id": state.trace_id,
            "session_key": state.session_key,
            # Every part's size, DERIVED from the one list — so a part added later
            # cannot be silently unmeasured the way `capabilities` was.
            **part_lens,
            # Not parts of the composed prompt, and deliberately still reported:
            # the banner rides the turn (state.pending_banner) and memory_len is
            # D01.1's comparison against the profile that replaced it.
            "banner_len": len(banner),
            "memory_len": len(state.memory_context or ""),
            "system_len": prompt_chars,
            "prompt_hash": prompt_hash,
        }},
    )
    if _prompt_store is not None and state.conversation_id and system_prompt:
        try:
            await _prompt_store.save(
                session_key=state.session_key, owl_name=state.owl_name,
                conversation_id=state.conversation_id, prompt_text=system_prompt,
                model_window=model_window,
            )
        except Exception as exc:  # a failed freeze costs a rebuild, never the turn
            log.engine.error(
                "[pipeline] assemble: prompt freeze FAILED — next turn rebuilds",
                exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
            )
    log.engine.info(
        "[pipeline] assemble: prompt source",
        extra={"_fields": {
            "trace_id": state.trace_id, "session_key": state.session_key,
            "conversation_id": state.conversation_id, "owl": state.owl_name,
            "source": "cold_build", "system_len": prompt_chars,
            "prompt_hash": prompt_hash,
        }},
    )
    return state.evolve(
        system_prompt=system_prompt,
        model_window=model_window,
        pending_banner=banner,
    )

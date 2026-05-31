"""Pipeline step: assemble — build the final system prompt (persona + DNA + memory).

RC-B fix: the pipeline previously sent only `memory_context` as the system
prompt, so the owl persona/DNA never reached the model. This step composes the
owl's persona + DNA-modulated directives (via owls/dna_injector) with the
recalled memory blocks classify produced.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.owls.dna_injector import DNAPromptInjector
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState

_injector = DNAPromptInjector()


async def run(state: PipelineState) -> PipelineState:
    log.engine.debug(
        "[pipeline] assemble: entry", extra={"_fields": {"trace_id": state.trace_id}}
    )
    services = get_services()
    registry = services.owl_registry
    persona = ""
    if registry is not None:
        try:
            manifest = registry.get(state.owl_name)
            persona = _injector.inject(manifest, manifest.dna)
            log.engine.debug(
                "[pipeline] assemble: persona resolved",
                extra={"_fields": {"owl": state.owl_name, "persona_len": len(persona)}},
            )
        except Exception as exc:  # B5 — unknown owl must not blank the prompt
            log.engine.warning(
                "[pipeline] assemble: persona lookup failed — memory-only prompt",
                exc_info=exc, extra={"_fields": {"owl": state.owl_name}},
            )
    else:
        log.engine.debug(
            "[pipeline] assemble: no owl_registry wired — memory-only prompt",
            extra={"_fields": {"owl": state.owl_name}},
        )
    parts = [p for p in (persona, state.memory_context) if p]
    system_prompt = "\n\n".join(parts) or None
    log.engine.debug(
        "[pipeline] assemble: exit",
        extra={"_fields": {
            "trace_id": state.trace_id,
            "persona_len": len(persona),
            "system_len": len(system_prompt or ""),
        }},
    )
    return state.evolve(system_prompt=system_prompt)

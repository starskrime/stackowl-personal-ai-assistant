"""DNAPromptInjector — modulates system prompts based on OwlDNA traits."""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.owls.dna import OwlDNA
from stackowl.owls.manifest import OwlAgentManifest

_HIGH_THRESHOLD = 0.7
_LOW_THRESHOLD = 0.3

# These are LLM-behaviour directives appended to the *system* prompt — they
# steer how the model responds, not what surface language it uses, so they
# stay neutral / register-only. The owl still mirrors the user's language at
# runtime; only the *behaviour* is modulated.
_HIGH_DIRECTIVES: tuple[tuple[str, str], ...] = (
    (
        "challenge_level",
        "Respond with skepticism when claims are unsupported; press for evidence and push back on weak arguments.",
    ),
    (
        "curiosity",
        "When intent or scope is ambiguous but the most likely action is reversible, "
        "act on the most likely interpretation and state your assumption — ask the "
        "user a clarifying question only when the action is irreversible or expensive.",
    ),
    (
        "formality",
        "Maintain a formal register and structured phrasing throughout the response.",
    ),
    (
        "creativity",
        "Explore unconventional approaches and surface non-obvious alternatives alongside the standard solution.",
    ),
    (
        "precision",
        "State specific, verifiable claims; cite sources, file paths, line numbers, or measurements whenever possible.",
    ),
    (
        "completion_drive",
        "Pursue the goal to completion: when one path is blocked, try an "
        "alternative approach and act on the next concrete step rather than "
        "stopping to ask. Persist until the task is genuinely done; surrender "
        "only after available options are exhausted, and then report honestly "
        "what was tried — never claim success you did not achieve.",
    ),
)

_LOW_DIRECTIVES: tuple[tuple[str, str], ...] = (
    (
        "verbosity",
        "Keep responses concise — prefer short paragraphs and avoid restating the question.",
    ),
    (
        "formality",
        "Use a casual, conversational register.",
    ),
)

# Traits whose HIGH directive backfires on / overloads a weak (small-window) model:
# precision makes it fabricate citations; challenge/curiosity/creativity push
# behaviours it follows poorly. Suppressed ONLY on the lean path; capable models
# keep the full set.
_LEAN_SUPPRESSED_TRAITS: frozenset[str] = frozenset(
    {"precision", "challenge_level", "curiosity", "creativity"}
)


class DNAPromptInjector:
    """Append trait-modulated instructions to an owl's system prompt.

    The injector is intentionally narrow — it only adds *behavioural*
    directives, never user-facing copy. The owl continues to mirror the
    user's language because nothing here forces a particular reply tongue.
    Neutral DNA (every trait near 0.5) returns the system prompt unchanged.
    """

    def inject(self, manifest: OwlAgentManifest, dna: OwlDNA, *, lean: bool = False) -> str:
        """Return ``manifest.system_prompt`` with DNA-driven directives appended.

        When ``lean`` (small-window model), directives that backfire on / overload a
        weak model (``_LEAN_SUPPRESSED_TRAITS``) are skipped; the cheap register/length
        directives (formality, verbosity) still apply. ``lean=False`` is byte-identical
        to the prior behaviour."""
        from stackowl.owls.directive_latch import DIRECTIVE_LATCH

        log.engine.debug(
            "[dna] injector.inject: entry",
            extra={"_fields": {"owl": manifest.name, "lean": lean}},
        )
        directives: list[str] = []
        for trait, directive in _HIGH_DIRECTIVES:
            if lean and trait in _LEAN_SUPPRESSED_TRAITS:
                continue
            value = float(getattr(dna, trait))
            if DIRECTIVE_LATCH.high_state(manifest.name, trait, value):
                directives.append(directive)
        for trait, directive in _LOW_DIRECTIVES:
            value = float(getattr(dna, trait))
            if DIRECTIVE_LATCH.low_state(manifest.name, trait, value):
                directives.append(directive)
        if not directives:
            log.engine.debug(
                "[dna] injector.inject: exit — no modulation",
                extra={"_fields": {"owl": manifest.name, "lean": lean}},
            )
            return manifest.system_prompt
        joined = "\n- ".join(directives)
        result = f"{manifest.system_prompt}\n\nBehavioural modulation (from owl DNA):\n- {joined}"
        log.engine.debug(
            "[dna] injector.inject: exit — directives appended",
            extra={"_fields": {"owl": manifest.name, "lean": lean, "directive_count": len(directives)}},
        )
        return result

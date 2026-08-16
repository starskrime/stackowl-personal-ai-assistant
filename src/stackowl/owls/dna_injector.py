"""DNAPromptInjector — modulates system prompts based on OwlDNA traits."""

from __future__ import annotations

import unicodedata

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
        # F-53: act-first / anti-over-clarify is now an UNCONDITIONAL charter
        # principle (see owls/base_prompt.behavioral_charter), so it no longer
        # lives here gated behind a high-curiosity band. The curiosity trait now
        # governs only EXPLORATION BREADTH — how widely the owl investigates
        # before settling — independent of the act-vs-ask decision.
        "curiosity",
        "Explore the problem broadly: consider adjacent angles, gather the context "
        "that matters, and surface relevant options the user did not name before "
        "settling on an approach.",
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


def _with_identity(manifest: OwlAgentManifest) -> str:
    """Prefix the persona with the owl's own SPOKEN name when it has been renamed.

    BAKIR, 2026-08-16: renaming an owl "fails or lies by saying updated".
    MEASURED: ``owl_build action=rename`` is durable and correct — the live YAML
    carried ``{name: secretary, display_name: Friday}`` and ``manifest.display``
    returned ``'Friday'``. But ``display`` — "the name to show/speak" — had exactly
    two readers in the tree, the spoken-name RESOLVER and the ``owls_list`` tool.
    Neither is the prompt. The persona handed to the model was 291 chars, opened
    with "You are the Secretary", and contained "Friday" nowhere, so the model
    could not possibly answer to its new name. A write with no reader.

    Only fires when a display_name is set AND actually differs from the routing
    slug (case-insensitively, NFC-normalised): an owl that was never renamed gets a
    byte-identical prompt, and "secretary"/"Secretary" is not a rename. The slug is
    stated too — the model is addressed by it internally and needs both halves to
    self-reference correctly in handoffs.

    LAW 1 SAFE: this lives in the STABLE base prompt and changes only when
    display_name changes, which already invalidates the frozen prompt. Same event,
    not a new one.
    """
    display = (manifest.display_name or "").strip()
    slug = (manifest.name or "").strip()
    if not display:
        return manifest.system_prompt
    if unicodedata.normalize("NFC", display).casefold() == unicodedata.normalize(
        "NFC", slug
    ).casefold():
        return manifest.system_prompt
    return (
        f"Your name is {display}. Always identify yourself as {display} — "
        f'"{slug}" is only your internal routing id, not your name.\n\n'
        f"{manifest.system_prompt}"
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
        # Fold the behavioural guardrail into the base prompt FIRST (design
        # decision 4), so it survives whether or not DNA also modulates. Empty
        # boundaries → byte-identical to the prior behaviour.
        base = _with_identity(manifest)
        boundaries = (manifest.boundaries or "").strip()
        if boundaries:
            base = f"{base}\n\nBoundaries: {boundaries}"

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
            return base
        joined = "\n- ".join(directives)
        result = f"{base}\n\nBehavioural modulation (from owl DNA):\n- {joined}"
        log.engine.debug(
            "[dna] injector.inject: exit — directives appended",
            extra={"_fields": {"owl": manifest.name, "lean": lean, "directive_count": len(directives)}},
        )
        return result

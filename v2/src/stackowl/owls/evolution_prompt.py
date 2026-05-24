"""EvolutionPromptBuilder — builds the LLM messages that suggest DNA deltas."""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.providers.base import Message


class EvolutionPromptBuilder:
    """Build the prompt asking an LLM to suggest DNA trait deltas.

    Output is two ``Message``s — a system message framing the task and a
    user message embedding the owl's current trait values plus recent
    conversation excerpts. The LLM is asked for a JSON object mapping each
    trait to a float delta in ``[-0.1, 0.1]``.
    """

    def build(
        self,
        owl_name: str,
        manifest: OwlAgentManifest,
        conversation_excerpts: list[str],
    ) -> list[Message]:
        """Return the message list for the evolution call."""
        log.engine.debug(
            "[dna] prompt.build: entry",
            extra={
                "_fields": {
                    "owl": owl_name,
                    "excerpt_count": len(conversation_excerpts),
                }
            },
        )
        dna = manifest.dna
        trait_lines = [
            f"  - {trait}: {value:.3f}"
            for trait, value in (
                ("challenge_level", dna.challenge_level),
                ("verbosity", dna.verbosity),
                ("curiosity", dna.curiosity),
                ("formality", dna.formality),
                ("creativity", dna.creativity),
                ("precision", dna.precision),
            )
        ]
        excerpts_block = (
            "\n\n".join(f"[{i + 1}] {text}" for i, text in enumerate(conversation_excerpts))
            if conversation_excerpts
            else "(no recent conversation excerpts)"
        )
        system = Message(
            role="system",
            content=(
                "You evolve owl personalities by suggesting small adjustments to "
                "personality trait values. Each trait is a float in [0.0, 1.0]. "
                "You return ONLY a JSON object with delta values."
            ),
        )
        user = Message(
            role="user",
            content=(
                f"Owl name: {owl_name}\n"
                f"Role: {manifest.role}\n\n"
                "Current DNA trait values:\n" + "\n".join(trait_lines) + "\n\n"
                "Recent conversation excerpts:\n"
                f"{excerpts_block}\n\n"
                "Based on these interactions, suggest a delta in the inclusive range "
                "[-0.1, 0.1] for each trait. Return a JSON object exactly in this shape:\n"
                '{"challenge_level": 0.05, "verbosity": -0.03, "curiosity": 0.00, '
                '"formality": 0.0, "creativity": 0.02, "precision": 0.0}\n'
                "Output ONLY the JSON object — no prose, no markdown fences."
            ),
        )
        log.engine.debug(
            "[dna] prompt.build: exit",
            extra={"_fields": {"owl": owl_name, "messages": 2}},
        )
        return [system, user]

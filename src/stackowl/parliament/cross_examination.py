"""CrossExaminationPromptBuilder — round-2+ prompt construction.

Each owl receives the topic plus *all other owls'* prior responses (its own
position is omitted from the "another participant said" block — the model
already has its own context). Any user interjections are surfaced verbatim
with a clear tag. The prompt template is intentionally structural (system
prompt to an LLM) and contains no content-analysis stopwords.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.parliament.models import ParliamentRound


class CrossExaminationPromptBuilder:
    """Builds per-owl prompts for Parliament rounds 2 and beyond."""

    def build(
        self,
        topic: str,
        owl_name: str,
        prior_rounds: list[ParliamentRound],
        interjections: list[str],
    ) -> str:
        """Return the full prompt string for ``owl_name`` in the next round.

        The prompt is composed of:

        1. Topic statement.
        2. Each prior round's responses from *other* owls, tagged
           ``[<owl_name>]:`` so the participant can address them directly.
        3. Any user interjections, tagged ``[User interjection]:``.
        4. Instruction to respond to specific points and refine its position.
        """
        log.parliament.debug(
            "[parliament] cross_examination.build: entry",
            extra={
                "_fields": {
                    "owl_name": owl_name,
                    "prior_rounds": len(prior_rounds),
                    "interjections": len(interjections),
                }
            },
        )

        parts: list[str] = []
        parts.append(f"Topic under debate:\n{topic}")

        if prior_rounds:
            parts.append("\n---\nPrior round responses from other participants:")
            for rnd in prior_rounds:
                parts.append(f"\n[Round {rnd.round_number}]")
                for other_name, response in rnd.genuine_responses().items():
                    if other_name == owl_name:
                        continue
                    parts.append(f"[{other_name}]: {response}")

        if interjections:
            parts.append("\n---\nUser interjections during this session:")
            for msg in interjections:
                parts.append(f"[User interjection]: {msg}")

        parts.append(
            "\n---\nRespond to the specific points raised by the other "
            "participants above. Address disagreements directly, refine your "
            "position where the critique is sound, and defend it where it is "
            "not. Be concise and analytical."
        )

        prompt = "\n".join(parts)
        log.parliament.debug(
            "[parliament] cross_examination.build: exit",
            extra={"_fields": {"owl_name": owl_name, "prompt_len": len(prompt)}},
        )
        return prompt

"""CriticScorerPromptBuilder — builds the LLM messages that score a task outcome.

Mirrors :class:`stackowl.owls.evolution_prompt.EvolutionPromptBuilder` —
same shape (system + user message), same minimal-JSON output contract.
Output is a single float in [0.0, 1.0]. Higher means the response better
addressed the user's request given the full trace (input + tools + errors).
"""

from __future__ import annotations

import json

from stackowl.infra.observability import log
from stackowl.memory.json_parser import parse_json_response
from stackowl.memory.outcome_store import TaskOutcome
from stackowl.providers.base import Message

__all__ = ["CriticScorerPromptBuilder", "parse_critic_response", "parse_json_response"]


class CriticScorerPromptBuilder:
    """Build the prompt asking an LLM to judge a single task outcome."""

    def build(self, outcome: TaskOutcome) -> list[Message]:
        """Return the message list for the critic call.

        We send the FULL trace (input + tool calls + response + errors)
        per the operator's vote — the critic needs visibility into HOW the
        agent arrived at the answer, not just the answer.
        """
        log.memory.debug(
            "[critic] prompt.build: entry",
            extra={"_fields": {"trace_id": outcome.trace_id, "owl": outcome.owl_name}},
        )

        trace_summary = {
            "owl_name": outcome.owl_name,
            "channel": outcome.channel,
            "latency_ms": int(outcome.latency_ms),
            "tool_call_count": outcome.tool_call_count,
            "failure_class": outcome.failure_class,
            "step_durations_ms": {k: int(v) for k, v in outcome.step_durations.items()},
            "succeeded_without_errors": outcome.success,
        }

        system = Message(
            role="system",
            content=(
                "You are a quality critic for an AI agent. Given the agent's "
                "full execution trace, you score how well the agent addressed "
                "the user's request. Return ONLY a JSON object — no prose, no "
                "markdown fences. The output schema is: "
                '{"score": <float 0.0-1.0>, "reason": <one short sentence>}.'
            ),
        )
        user = Message(
            role="user",
            content=(
                f"USER REQUEST:\n{outcome.input_text[:2000]}\n\n"
                f"AGENT RESPONSE:\n{outcome.response_text[:2000]}\n\n"
                f"EXECUTION TRACE:\n{json.dumps(trace_summary, indent=2)}\n\n"
                "Score the agent's response 0.0-1.0 on how well it addressed "
                "the request, considering:\n"
                "- Was the answer correct/useful (most important)?\n"
                "- Did it use tools efficiently (not too many, not too few)?\n"
                "- Did it complete without errors?\n"
                "- Was the latency reasonable for the complexity?\n\n"
                'Output exactly: {"score": 0.75, "reason": "..."}'
            ),
        )
        log.memory.debug(
            "[critic] prompt.build: exit",
            extra={"_fields": {"trace_id": outcome.trace_id, "messages": 2}},
        )
        return [system, user]


def parse_critic_response(raw: str) -> float | None:
    """Pull the ``score`` float out of a critic response, returning None on parse failure.

    Delegates the fence-stripping + JSON extraction to the shared
    :func:`parse_json_response` helper; just enforces the score-specific
    type+clamp rules here.
    """
    obj = parse_json_response(raw, required_keys=["score"])
    if obj is None:
        return None
    score = obj.get("score")
    if not isinstance(score, int | float):
        return None
    # Clamp to the documented range.
    return max(0.0, min(1.0, float(score)))

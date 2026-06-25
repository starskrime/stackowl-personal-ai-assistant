"""ReflectionPromptBuilder + reflection-response parser.

Mirrors :class:`CriticScorerPromptBuilder` exactly — 2-message system+user
template, returns a JSON object with the agreed schema. The shared
:func:`parse_json_response` helper handles the fence-stripping and validation.

The reflection's purpose is Reflexion-style (Shinn 2023): given the full
trace of a task that went WELL, generate a short "what worked / what to repeat"
artifact that becomes retrievable for future runs facing similar query
semantics. Positive-only: the platform learns from successes, not failures.
"""

from __future__ import annotations

import json

from stackowl.infra.observability import log
from stackowl.memory.json_parser import parse_json_response
from stackowl.memory.outcome_store import TaskOutcome
from stackowl.providers.base import Message


class ReflectionPromptBuilder:
    """Build the prompt asking an LLM to reflect on one task outcome."""

    def build(self, outcome: TaskOutcome) -> list[Message]:
        """Return the message list for the reflection call.

        Sends the full trace (input + response + outcome metrics) so the LLM can
        identify the SPECIFIC winning move worth repeating.
        """
        # 1. ENTRY
        log.memory.debug(
            "[reflection] prompt.build: entry",
            extra={"_fields": {
                "trace_id": outcome.trace_id, "owl": outcome.owl_name,
                "failure_class": outcome.failure_class,
                "quality_score": outcome.quality_score,
            }},
        )

        trace_summary = {
            "owl_name": outcome.owl_name,
            "channel": outcome.channel,
            "latency_ms": int(outcome.latency_ms),
            "tool_call_count": outcome.tool_call_count,
            "failure_class": outcome.failure_class,
            "quality_score": outcome.quality_score,
            "step_durations_ms": {k: int(v) for k, v in outcome.step_durations.items()},
            "succeeded_without_errors": outcome.success,
        }

        system = Message(
            role="system",
            content=(
                "You are a learning coach for an AI agent. Given a completed "
                "task that went WELL (succeeded with high quality), you write a "
                "short reflection capturing what worked and a concrete winning "
                "strategy to repeat next time. Stay positive and forward-looking "
                "— never frame anything as a failure or a limitation.\n\n"
                "Return ONLY a JSON object — no prose, no markdown fences. "
                "The schema is:\n"
                '{"summary": "<one-sentence what worked well>", '
                '"suggested_strategy": "<one-sentence winning approach to repeat>"}'
            ),
        )
        user = Message(
            role="user",
            content=(
                f"USER REQUEST:\n{outcome.input_text[:2000]}\n\n"
                f"AGENT RESPONSE:\n{outcome.response_text[:2000]}\n\n"
                f"EXECUTION TRACE:\n{json.dumps(trace_summary, indent=2)}\n\n"
                "Write the reflection. Be specific about what worked and the "
                "concrete approach worth repeating. Avoid generic praise like "
                "'good job' — name the actual tool, prompt move, or decision that "
                "made this succeed.\n\n"
                'Output exactly: {"summary": "...", "suggested_strategy": "..."}'
            ),
        )
        # 4. EXIT
        log.memory.debug(
            "[reflection] prompt.build: exit",
            extra={"_fields": {"trace_id": outcome.trace_id, "messages": 2}},
        )
        return [system, user]


def parse_reflection_response(raw: str) -> tuple[str, str] | None:
    """Parse the LLM reflection response into (summary, suggested_strategy).

    Returns None if the response doesn't contain both keys with non-empty
    string values. Delegates fence-stripping/JSON-extraction to the shared
    :func:`parse_json_response` helper.
    """
    obj = parse_json_response(raw, required_keys=["summary", "suggested_strategy"])
    if obj is None:
        return None
    summary = obj.get("summary")
    suggested = obj.get("suggested_strategy")
    if not isinstance(summary, str) or not summary.strip():
        return None
    if not isinstance(suggested, str):
        suggested = ""
    return summary.strip(), suggested.strip()

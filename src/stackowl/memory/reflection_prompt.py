"""ReflectionPromptBuilder + reflection-response parser.

Mirrors :class:`CriticScorerPromptBuilder` exactly — 2-message system+user
template, returns a JSON object with the agreed schema. The shared
:func:`parse_json_response` helper handles the fence-stripping and validation.

The reflection's purpose is Reflexion-style (Shinn 2023): given the full
trace of a task that went wrong, generate a short "what would I do
differently next time" artifact that becomes retrievable for future runs
facing similar failure_class or query semantics.
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

        Sends the full trace (input + response + outcome metrics + failure_class)
        so the LLM can identify the SPECIFIC mistake / suboptimality.
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
                "You are a learning critic for an AI agent. Given a completed "
                "task that went wrong (failed or scored low quality), you write "
                "a short reflection identifying the specific mistake and a "
                "concrete suggested strategy for next time.\n\n"
                "Return ONLY a JSON object — no prose, no markdown fences. "
                "The schema is:\n"
                '{"summary": "<one-sentence what went wrong>", '
                '"suggested_strategy": "<one-sentence what to try differently>"}'
            ),
        )
        user = Message(
            role="user",
            content=(
                f"USER REQUEST:\n{outcome.input_text[:2000]}\n\n"
                f"AGENT RESPONSE:\n{outcome.response_text[:2000]}\n\n"
                f"EXECUTION TRACE:\n{json.dumps(trace_summary, indent=2)}\n\n"
                "Write the reflection. Be specific about what went wrong "
                "and what concrete change would help in the future. "
                "Avoid generic advice like 'try harder' — name the actual "
                "tool, prompt move, or decision that should change.\n\n"
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

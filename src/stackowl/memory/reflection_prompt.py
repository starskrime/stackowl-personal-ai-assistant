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


#: The keys :func:`parse_reflection_response` demands. ONE source — the parser
#: below and :func:`describe_parse_failure` both read it, so the diagnostic can
#: never disagree with the check it is diagnosing.
REFLECTION_REQUIRED_KEYS = ("summary", "suggested_strategy")


def describe_parse_failure(raw: str) -> dict[str, object]:
    """Say WHY a reflection response did not parse, in one structured record.

    The failure line used to carry ``result.content[:200]`` and nothing else.
    MEASURED 2026-08-22 over the retained 8-day window: 50 of 51 failure records
    carried a preview of exactly 200 characters — every one truncated at the cap —
    and 49 showed ``{"summary": "..."`` cut off mid-sentence. That looks like a
    root cause and is not one: a reflection's ``summary`` alone runs well past 200
    characters, so ``suggested_strategy`` is simply beyond the window, and every
    response — well-formed or not — looks identical in that field.

    A panel lens read those 50 previews as proof the second key was absent and
    proposed relaxing the requirement. On that evidence it was a guess. This is the
    same defect as ``D05.8``'s ``dropped[:20]``: a truncated field read as a
    complete answer. So the fix is to make the record answer the question rather
    than to act on a prefix.

    Never raises — it runs inside a failure handler and must not be able to add a
    second failure to the one being reported.
    """
    out: dict[str, object] = {
        "shape": "unknown", "keys": None, "missing": None, "chars": 0,
    }
    try:
        out["chars"] = len(raw)
        text = (raw or "").strip()
        if not text:
            out["shape"] = "empty"
            return out
        obj = parse_json_response(text)
        if obj is not None:
            keys = sorted(str(k) for k in obj)
            out["shape"] = "json_object"
            out["keys"] = keys
            out["missing"] = [k for k in REFLECTION_REQUIRED_KEYS if k not in obj]
            return out
        # Not a usable object. Separate "the model wrote prose" from "the JSON
        # was cut off", because those want opposite fixes and the old preview
        # could not tell them apart.
        stripped = text.lstrip("`").lstrip()
        if stripped.startswith("json"):
            stripped = stripped[4:].lstrip()
        if stripped.startswith("{"):
            out["shape"] = "json_truncated" if stripped.count("{") > stripped.count("}") \
                else "json_not_object"
        elif stripped.startswith("["):
            out["shape"] = "json_not_object"
        else:
            out["shape"] = "not_json"
        return out
    except Exception:  # pragma: no cover — a diagnostic may never raise
        out["shape"] = "describe_failed"
        return out


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

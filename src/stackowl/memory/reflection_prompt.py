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
import re

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

#: What a reflection cannot do without. `suggested_strategy` is in
#: REFLECTION_REQUIRED_KEYS because that tuple describes what the PROMPT asks for
#: and is what `describe_parse_failure` reports against; it is not what parsing
#: requires. See the comment in `parse_reflection_response`.
_SUMMARY_KEY = "summary"


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
        # was cut off" from "it is JSON-shaped but malformed", because those want
        # different fixes and the old preview could not tell them apart.
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
        if out["shape"] == "json_not_object":
            # The first production record carried shape=json_not_object, chars=502,
            # keys=None — brace-balanced, not truncated, and still unparseable. That
            # already refuted the "a required key was missing" theory, but it did not
            # say WHAT was malformed. json's own error names the character and the
            # offset, which is the difference between another guess and a fix.
            try:
                json.loads(stripped)
            except ValueError as exc:
                out["json_error"] = str(exc)[:160]
        return out
    except Exception:  # pragma: no cover — a diagnostic may never raise
        out["shape"] = "describe_failed"
        return out


def _escape_stray_quotes(text: str) -> str:
    """Escape literal ``"`` characters the model left unescaped inside a string.

    THE ONE FAULT FOUR OF FIVE PRODUCTION FAILURES SHARE. Gathered from the core's
    own log after `describe_parse_failure` shipped:
      chars=691  "Expecting ',' delimiter: line 1 column 310"
      chars=482  "Expecting ',' delimiter: line 1 column 240"
      chars=466  "Unterminated string starting at: line 1 column 13"
    Reproduced deliberately: an unescaped quote mid-string yields exactly
    ``Expecting ',' delimiter``. A raw newline would yield ``Invalid control
    character``, which has NOT appeared in production — so this is targeted at the
    fault that actually occurs, not a general "repair the model's JSON" heuristic.

    THE RULE. Walk the text tracking string state. Inside a string, a ``"`` is a
    real terminator only if the next non-space character is one of ``,:}]`` or the
    end of input — that is the only place JSON permits a string to end. Anything
    else means the model wrote a literal quote, so escape it.

    Deliberately conservative: it runs ONLY after strict parsing has already
    failed, so it can recover a response and can never regress one that already
    parsed, and it leaves correctly-escaped quotes exactly as they are.

    KNOWN LIMIT, pinned by test rather than papered over: a stray quote FOLLOWED BY
    A COMMA is byte-for-byte indistinguishable from a string ending and the next
    element beginning, so it is not recovered. That shape has not appeared in any
    measured failure — all four repairable ones had the quote followed by a letter
    or a space — and building for an unobserved case is how a repair pass grows
    into something that corrupts good input.
    """
    out: list[str] = []
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            continue
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if ch == '"':
            nxt = next((c for c in text[i + 1:] if not c.isspace()), "")
            if nxt in (",", ":", "}", "]", ""):
                out.append(ch)
                in_string = False
            else:
                out.append('\\"')
            continue
        out.append(ch)
    return "".join(out)


#: The summary value of a response that stopped inside it. Anchored on the key so
#: prose can never reach the repair, and greedy to the end because there IS no
#: closing quote — that is the whole condition being repaired.
_TRUNCATED_SUMMARY_RE = re.compile(r'"summary"\s*:\s*"(.*)$', re.DOTALL)

#: Everything up to the last sentence terminator. A summary cut mid-clause can
#: invert its own meaning — "the agent did not" — and it is injected into later
#: prompts, so the fragment after the last full stop is dropped rather than kept.
_UP_TO_LAST_SENTENCE_RE = re.compile(r"^.*[.!?](?=[\s\"]|$)", re.DOTALL)


def _summary_from_truncated(raw: str) -> str | None:
    """The completed sentences of a summary the model stopped writing, or None.

    MEASURED 2026-08-31: 35 of 64 reflection parse failures carry
    ``Unterminated string starting at ...`` — the response ends inside the summary.
    Output tokens on those traces run 2 to 2,912 with p50 183, so nothing is pinned
    at a ceiling and ``_output_cap`` is window-sized: the model simply stops.

    Deliberately NOT a general JSON repair. It reads ONE key, requires that key's
    string to be genuinely unterminated, and returns text rather than an object —
    so it cannot invent a well-formed response the model never produced.
    """
    text = raw.strip().lstrip("`").lstrip()
    if text.startswith("json"):
        text = text[4:].lstrip()
    if not text.startswith("{"):
        return None  # prose is not a truncated reflection
    match = _TRUNCATED_SUMMARY_RE.search(text)
    if match is None:
        return None
    partial = match.group(1)
    # An unescaped closing quote means the string DID terminate and the fault is
    # elsewhere — that is the stray-quote repair's job, not this one.
    if re.search(r'(?<!\\)"', partial):
        return None
    partial = partial.replace('\\"', '"').replace("\\n", " ").replace("\\\\", "\\")
    sentences = _UP_TO_LAST_SENTENCE_RE.match(partial)
    if sentences is None:
        return None  # no complete sentence — nothing here is trustworthy
    recovered = sentences.group(0).strip()
    if not recovered:
        return None
    log.memory.info(
        "[reflection] parse: recovered the completed sentences of a truncated "
        "response",
        extra={"_fields": {
            "recovered_chars": len(recovered),
            "dropped_chars": len(partial) - len(recovered),
        }},
    )
    return recovered


def parse_reflection_response(raw: str) -> tuple[str, str] | None:
    """Parse the LLM reflection response into (summary, suggested_strategy).

    Returns None if the response doesn't contain both keys with non-empty
    string values. Delegates fence-stripping/JSON-extraction to the shared
    :func:`parse_json_response` helper.

    ONE RETRY, and only after strict parsing has failed: four of five measured
    production failures were a literal ``"`` left unescaped inside the summary
    string, so :func:`_escape_stray_quotes` gets a second attempt. Ordering
    matters — a response that already parses never reaches the repair, so this
    can recover and cannot regress.
    """
    # SUMMARY IS THE ONLY KEY THIS FUNCTION ACTUALLY NEEDS, and it already said so
    # four lines from here: `if not isinstance(suggested, str): suggested = ""`.
    # The gate demanded a key the body handles the absence of, and BOTH readers
    # guard on it too — classify.py:284 writes the strategy line only `if
    # r.suggested_strategy`, and reflection_writer_handler.py:424 appends "Repeat:"
    # only `if suggested_strategy`. MEASURED 2026-08-31: 15 of 64 parse failures
    # were a VALID object carrying keys ['summary'] and missing
    # ['suggested_strategy'] — a summary the model wrote well, discarded for a key
    # nothing downstream requires.
    obj = parse_json_response(raw, required_keys=[_SUMMARY_KEY])
    if obj is None:
        repaired = _escape_stray_quotes(raw)
        if repaired != raw:
            obj = parse_json_response(
                repaired, required_keys=list(REFLECTION_REQUIRED_KEYS)
            )
            if obj is not None:
                log.memory.info(
                    "[reflection] parse: recovered a response whose only fault was "
                    "an unescaped quote",
                    extra={"_fields": {"chars": len(raw)}},
                )
    if obj is None:
        # LAST, so a response that parses by any stricter route never reaches it —
        # the same ordering rule `_escape_stray_quotes` states, for the same reason.
        recovered = _summary_from_truncated(raw)
        return (recovered, "") if recovered else None
    summary = obj.get("summary")
    suggested = obj.get("suggested_strategy")
    if not isinstance(summary, str) or not summary.strip():
        return None
    if not isinstance(suggested, str):
        suggested = ""
    return summary.strip(), suggested.strip()

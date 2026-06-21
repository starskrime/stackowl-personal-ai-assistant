"""Structured-result builders for ``delegate_task`` (B2 split from the tool).

Pure, side-effect-free shaping of the tool's JSON result envelope: success/refusal
records, the provenance footer, and the sub-task composition. Kept here so
``delegate_task.py`` stays under the B2 line cap and the result contract is
testable in isolation. Each builder returns a frozen
:class:`stackowl.tools.base.ToolResult`; none raise.
"""

from __future__ import annotations

import json
import time

from stackowl.infra.observability import log
from stackowl.tools.base import ToolResult


def compose_sub_task(goal: str, context: str | None) -> str:
    """Join the goal with optional caller-supplied context for the specialist."""
    if not context:
        return goal
    return f"{goal}\n\nContext:\n{context}"


def provenance_footer(target: str) -> str:
    """Short footer flagging the result as ``target``'s delegated sub-run."""
    return f"\n\n— delegated to '{target}' (sub-run); result above is {target}'s, not the caller's."


def ok_result(record: dict[str, object], t0: float, *, note: str) -> ToolResult:
    """Wrap a structured ``record`` into a success ToolResult (logs exit)."""
    duration_ms = (time.monotonic() - t0) * 1000
    log.tool.info(
        "delegate_task.execute: exit",
        extra={"_fields": {"success": True, "status": record.get("status"), "duration_ms": duration_ms}},
    )
    payload = json.dumps({"note": note, "record": record}, ensure_ascii=False)
    return ToolResult(success=True, output=payload, duration_ms=duration_ms)


def refusal_result(t0: float, *, reason: str, detail: str) -> ToolResult:
    """A structured (success=True) refusal — a safety rail, not a crash."""
    return ok_result({"status": "refused", "reason": reason, "detail": detail}, t0, note=detail)


def cycle_result(t0: float, *, target: str, chain: tuple[str, ...]) -> ToolResult:
    """Delegation would form a cycle — refuse before acquiring any slot."""
    return ok_result(
        {
            "status": "cycle",
            "to_owl": target,
            "detail": (
                f"delegating to '{target}' would loop "
                f"({' -> '.join(chain)} -> {target}); "
                "do NOT delegate again — answer the user directly or say you cannot."
            ),
        },
        t0,
        note="delegation cycle prevented",
    )


def target_not_found_result(t0: float, *, to_owl: str) -> ToolResult:
    """Named target owl does not exist in the registry."""
    return ok_result(
        {
            "status": "target_not_found",
            "to_owl": to_owl,
            "detail": (
                f"no owl named '{to_owl}' exists; do NOT delegate again — "
                "answer directly or tell the user you cannot."
            ),
        },
        t0,
        note="delegation target not found",
    )


def child_error_result(t0: float, *, target: str, detail: str) -> ToolResult:
    """Specialist ran but terminated with an error."""
    return ok_result(
        {
            "status": "child_error",
            "to_owl": target,
            "detail": (
                f"specialist '{target}' failed "
                f"(specialist detail (untrusted): {detail}); "
                "do NOT delegate again — handle it yourself or tell the user."
            ),
            "result": "",
        },
        t0,
        note=f"{target} failed",
    )


def truncated_result(t0: float, *, target: str, result: str, detail: str) -> ToolResult:
    """Specialist answered but the answer was cut off by a resource cap."""
    return ok_result(
        {
            "status": "truncated",
            "to_owl": target,
            "result": result,
            "detail": (
                f"{target}'s answer was cut off by a resource cap; "
                "treat as INCOMPLETE."
            ),
        },
        t0,
        note=f"{target} answer truncated",
    )


def recovered_result(t0: float, *, original: str, via: str, result: str) -> ToolResult:
    """Original target was unavailable; the fallback specialist handled it instead.

    Attributed lead-in flags the substitution so the model can surface it if relevant.
    English model-facing note (i18n for user-visible text is T9).
    """
    lead = f"[{original} was unavailable, so {via} handled this instead:]\n"
    return ok_result(
        {
            "status": "recovered_via_secretary",
            "to_owl": via,
            "original": original,
            "result": lead + result + provenance_footer(via),
        },
        t0,
        note=f"recovered: {via} handled the sub-task after {original} failed",
    )


def error_result(msg: str, t0: float) -> ToolResult:
    """A failed ToolResult for invalid-argument cases (logs exit).

    Its sole call site is delegate_task's argument-validation failure — a PRE-
    EXECUTION refusal where no specialist was ever invoked, so
    side_effect_committed=False so it does not trip the give-up floor. (Mid-flight
    delegations that may have acted use the honest_uncertain/offtopic builders,
    which intentionally keep the default True.)
    """
    duration_ms = (time.monotonic() - t0) * 1000
    log.tool.info(
        "delegate_task.execute: exit",
        extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
    )
    return ToolResult(
        success=False, output="", error=msg,
        duration_ms=duration_ms, side_effect_committed=False,
    )


def _honest_failed_result(record: dict[str, object], msg: str, t0: float) -> ToolResult:
    """Internal helper: success=False terminal result with msg in both error and record.

    Mirrors child_error_result's record placement (JSON payload in output) but
    sets success=False because these builders represent delegations that did NOT
    deliver a usable answer, and PREFERRED to be honest failures rather than
    masked successes.
    """
    duration_ms = (time.monotonic() - t0) * 1000
    log.tool.info(
        "delegate_task.execute: exit",
        extra={"_fields": {"success": False, "status": record.get("status"), "duration_ms": duration_ms}},
    )
    payload = json.dumps({"record": record}, ensure_ascii=False)
    return ToolResult(success=False, output=payload, error=msg, duration_ms=duration_ms)


def honest_uncertain_result(target: str, t0: float) -> ToolResult:
    """Delegation did not complete and may have partially performed a consequential action.

    Used when a timeout or ambiguous failure occurs mid-flight — we cannot know whether
    the specialist acted, so the parent must NOT auto-retry.
    """
    msg = (
        f"FAILED — delegation to '{target}' did not complete and may have partially performed a "
        "consequential action; it was NOT retried to avoid duplicating it. Do NOT retry "
        "automatically — verify state, or re-issue explicitly if safe."
    )
    return _honest_failed_result(
        {"status": "uncertain", "to_owl": target, "result": msg},
        msg,
        t0,
    )


def honest_offtopic_write_result(target: str, t0: float) -> ToolResult:
    """Specialist completed but its response did not address the request.

    Because the specialist can perform consequential (write) actions it was NOT
    re-delegated — it may have already acted.  Parent must verify state before retrying.
    """
    msg = (
        f"FAILED — '{target}' completed but its response did not address your request, and because "
        "it can perform consequential actions it was NOT re-delegated (it may have already acted). "
        "Verify state before retrying; do NOT auto-retry."
    )
    return _honest_failed_result(
        {"status": "off_topic", "to_owl": target, "result": msg},
        msg,
        t0,
    )


def honest_irrelevant_result(t0: float) -> ToolResult:
    """No available specialist could address the request at all.

    The parent should handle it directly with its own knowledge/tools or rephrase
    the sub-task — do NOT retry this delegation.
    """
    msg = (
        "FAILED — the delegated response(s) did not address your request and no available specialist "
        "could answer it. Do NOT retry this delegation. Handle it directly with your own "
        "knowledge/tools, or rephrase the sub-task more concretely."
    )
    return _honest_failed_result(
        {"status": "irrelevant", "result": msg},
        msg,
        t0,
    )

"""rca_verdict_router — Task 7: thin consumer that routes a verified
:class:`~stackowl.learning.failure_outcome_miner.RcaVerdict` (Task 6's
:class:`~stackowl.parliament.staged_rca.StagedRcaSession` output) into the
EXISTING gated fix/alternative machinery. Builds NO new execution path:

* A **"fix"** verdict is submitted as a
  :class:`~stackowl.tools.meta.tool_spec.LearnedToolSpec` through the REAL
  ``tool_build`` gate (``security_scan_gate`` + consent, UNCHANGED). The RCA
  PROPOSES a fix; ``tool_build``'s own gate still decides whether it is safe
  and whether a human approves it, and only THAT gate ever persists/registers
  anything. Most fixes are prose-only guidance with no literal command (the
  hypothesis/verifier prompts in ``staged_rca.py`` never ask for one) — that
  is the expected, common case, not a degraded one: the guidance still
  reaches the operator (the incident alert) and the learner
  (``FailureOutcomeMiner``), just not ``tool_build``.
* An **"alternative"** verdict only CONSULTS
  :func:`~stackowl.pipeline.capability_substitution.find_substitute` — a PURE,
  read-only decision function — to confirm whether a live, eligible sibling
  capability currently exists. It never runs the substitute itself.

``delegate_task.py``'s retry-once -> fallback-to-secretary ladder is
deliberately NOT wired here: it is a LIVE-TURN mechanism (a
``TraceContext``-scoped depth/session/channel and an ``A2ADelegator``
round-trip). Invoking it from a scheduler tick would require fabricating a
fake user-turn identity — exactly the escalation the task brief calls out
("faking a real user turn"). ``capability_substitution.find_substitute`` has
no such dependency (no ``TraceContext``, no consent, no execution), so it is
the alternative-consumption path instead.
"""

from __future__ import annotations

import re
import shlex
import time
from typing import TYPE_CHECKING, Literal

from stackowl.infra.observability import log
from stackowl.learning.failure_outcome_miner import RcaVerdict
from stackowl.pipeline.capability_substitution import find_substitute
from stackowl.tools.meta.tool_build import ToolBuildTool

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.tools.registry import ToolRegistry

# Matches the FIRST fenced ``` code block, else the first inline `...` span, in
# the hypothesis/verifier owl's free-text ``fix_pattern``. ponytail: a bounded,
# deterministic text extraction — no second LLM call to re-parse the prose —
# with a documented ceiling: only a LITERAL command the RCA owl already wrote
# verbatim is ever extracted, nothing is invented from the diagnosis text.
# ``validate_spec``/``security_scan_gate`` inside the REAL ``tool_build`` gate
# still fully re-vet whatever is extracted before anything is persisted.
_FENCED_RE = re.compile(r"```(?:\w+\n)?(.*?)```", re.DOTALL)
_INLINE_RE = re.compile(r"`([^`\n]+)`")


def extract_argv_from_fix(fix_pattern: str) -> list[str] | None:
    """Best-effort extraction of a literal shell command the RCA owl proposed
    VERBATIM inside its ``fix_pattern`` text (a fenced block, else the first
    inline span). Returns ``None`` when no parseable command is present — the
    common case for prose-only guidance. Never raises."""
    m = _FENCED_RE.search(fix_pattern) or _INLINE_RE.search(fix_pattern)
    if not m:
        return None
    try:
        argv = shlex.split(m.group(1).strip())
    except ValueError:  # unbalanced quotes etc — not a usable command
        return None
    return argv or None


async def consume_fix_verdict(verdict: RcaVerdict) -> str:
    """Attempt to submit *verdict* as a ``LearnedToolSpec`` through the REAL
    ``tool_build`` gate (``security_scan_gate`` + consent, UNCHANGED). This
    function never writes or registers anything itself — ``tool_build``'s own
    gate does, and only if it passes. Returns a short outcome string for
    logging (never raises).
    """
    # 1. ENTRY
    log.scheduler.debug(
        "[rca_router] consume_fix_verdict: entry",
        extra={"_fields": {"skill_name": verdict.skill_name}},
    )
    argv = extract_argv_from_fix(verdict.fix_pattern)
    # 2. DECISION — no literal command proposed: honestly skip tool_build. The
    # miner (wired separately) still captures the guidance as a learned skill.
    if argv is None:
        log.scheduler.info(
            "[rca_router] consume_fix_verdict: no literal command in fix_pattern — "
            "skipping tool_build (miner still captures the guidance as a skill)",
            extra={"_fields": {"skill_name": verdict.skill_name}},
        )
        return "skipped_no_argv"
    # 3. STEP — the REAL gate: pydantic parse -> validate_spec -> collision
    # check -> security_scan_gate -> consent -> persist -> register live.
    result = await ToolBuildTool().execute(
        action="create",
        name=verdict.skill_name,
        description=(verdict.description or verdict.fix_pattern[:200]),
        params=[],
        argv_template=argv,
    )
    # 4. EXIT
    log.scheduler.info(
        "[rca_router] consume_fix_verdict: tool_build attempted",
        extra={"_fields": {
            "skill_name": verdict.skill_name,
            "success": result.success,
            "detail": (result.output or result.error or "")[:200],
        }},
    )
    return "submitted" if result.success else f"refused: {result.error or result.output}"


async def consume_alternative_verdict(
    verdict: RcaVerdict, *, tool_registry: ToolRegistry | None,
) -> str:
    """Consult (READ-ONLY) whether a live substitute exists for the failed
    capability. Calls the SAME pure decision function the in-turn self-heal
    substitution layer uses — never executes the substitute itself (no new
    execution path; actually RUNNING a substitute call only ever happens
    inside a live turn's own recovery path).

    ``verdict.capability_class`` only resolves to a concrete registered tool
    when the cluster's capability grain IS a raw tool name (the documented
    "capability class of one" fallback in ``failure_outcome_miner.py``) —
    when it is instead a shared ``capability_tag`` string,
    ``registry.get(capability_class)`` is ``None`` and this honestly reports
    "no substitute confirmed" (logged, not silently upgraded to a guess).
    """
    # 1. ENTRY
    log.scheduler.debug(
        "[rca_router] consume_alternative_verdict: entry",
        extra={"_fields": {"capability_class": verdict.capability_class}},
    )
    # 2. DECISION — no registry, nothing to consult.
    if tool_registry is None:
        log.scheduler.warning(
            "[rca_router] consume_alternative_verdict: no tool_registry wired — cannot consult",
            extra={"_fields": {"capability_class": verdict.capability_class}},
        )
        return "no_registry"
    # 3. STEP — the SAME pure decision function the in-turn substitution layer
    # uses. ``already_substituted`` starts empty (no turn to accumulate against);
    # ``in_bounds`` has no owl-authz bound-set for a background incident, so
    # every candidate is in-bounds — this only ever CONFIRMS a candidate exists,
    # it never runs one.
    substitute = find_substitute(
        verdict.capability_class, {},
        registry=tool_registry, in_bounds=lambda _name: True,
        already_substituted=set(),
    )
    # 4. EXIT
    if substitute is None:
        log.scheduler.info(
            "[rca_router] consume_alternative_verdict: no eligible sibling found",
            extra={"_fields": {"capability_class": verdict.capability_class}},
        )
        return "no_substitute"
    sibling_name, _built_args = substitute
    log.scheduler.info(
        "[rca_router] consume_alternative_verdict: eligible sibling confirmed",
        extra={"_fields": {
            "capability_class": verdict.capability_class, "sibling": sibling_name,
        }},
    )
    return f"substitute:{sibling_name}"


async def route_rca_verdict(
    verdict: RcaVerdict,
    kind: Literal["fix", "alternative"],
    *,
    tool_registry: ToolRegistry | None = None,
) -> None:
    """Task 7 entry point: dispatch a verified ``RcaVerdict`` by *kind*.

    Called from :class:`~stackowl.scheduler.handlers.incident_escalation.IncidentEscalationHandler`
    right after it produces a new verdict. Never raises (B5) — a consumer
    failure must never wedge the scheduler tick that called it.
    """
    t0 = time.monotonic()
    # 1. ENTRY
    log.scheduler.debug(
        "[rca_router] route_rca_verdict: entry",
        extra={"_fields": {
            "kind": kind, "capability_class": verdict.capability_class,
            "verified": verdict.verified,
        }},
    )
    # 2. DECISION — only a VERIFIED verdict is ever consumed (an unverified/
    # rejected one is treated exactly like "no verdict yet", same as the miner).
    if not verdict.verified:
        log.scheduler.debug(
            "[rca_router] route_rca_verdict: unverified verdict — no consumption",
            extra={"_fields": {"capability_class": verdict.capability_class}},
        )
        return
    try:
        # 3. STEP — dispatch to the matching real gate/consult.
        if kind == "fix":
            outcome = await consume_fix_verdict(verdict)
        else:
            outcome = await consume_alternative_verdict(verdict, tool_registry=tool_registry)
    except Exception as exc:  # B5 — a consumer failure must never wedge the tick
        log.scheduler.error(
            "[rca_router] route_rca_verdict: consumer failed",
            exc_info=exc,
            extra={"_fields": {"kind": kind, "capability_class": verdict.capability_class}},
        )
        return
    # 4. EXIT
    log.scheduler.info(
        "[rca_router] route_rca_verdict: exit",
        extra={"_fields": {
            "kind": kind, "outcome": outcome,
            "duration_ms": (time.monotonic() - t0) * 1000.0,
        }},
    )

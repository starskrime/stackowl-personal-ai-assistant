"""Self-healing turn supervisor: detection veto, never-empty floor, shared tally."""
from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.pipeline.persistence import PERSISTENCE_DIRECTIVE, is_structural_giveup


def tally_tool_outcomes(all_calls: list[dict[str, object]]) -> tuple[int, int]:
    """Count failed/successful tool calls from the AUTHORITATIVE typed ``failed`` bool.

    NEVER re-scan ``call["result"]`` for ``TOOL_FAILED_MARKER`` — the marker is
    stripped before the result is stored (``anthropic_provider.py:286`` /
    ``openai_provider.py``), so a re-scan is always False and the structural net
    would silently never fire.
    """
    failures = sum(1 for c in all_calls if bool(c.get("failed")))
    successes = sum(1 for c in all_calls if not bool(c.get("failed")))
    log.engine.debug(
        "supervisor.tally",
        extra={"_fields": {"failures": failures, "successes": successes}},
    )
    return failures, successes


def apply_structural_veto(
    *, judge_directive: str | None, all_calls: list[dict[str, object]], draft: str
) -> str | None:
    """Always-on structural veto over the judge's verdict.

    If the judge already returned a directive (it flagged a give-up), keep it.
    Otherwise compute the structural signal from the AUTHORITATIVE ``failed`` bools;
    if it's a give-up, OVERRIDE the judge's (possibly hallucinated) DELIVERED and
    inject the persistence directive. Catches a weak local judge returning a
    confident-but-wrong "delivered" — the actual Jetson failure mode.
    """
    if judge_directive is not None:
        return judge_directive
    failures, successes = tally_tool_outcomes(all_calls)
    if is_structural_giveup(tool_failures=failures, successful_tool_calls=successes, draft=draft):
        log.engine.debug("supervisor.veto: overriding judge DELIVERED on structural give-up")
        return PERSISTENCE_DIRECTIVE
    return None

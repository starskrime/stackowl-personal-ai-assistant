"""Self-healing turn supervisor: detection veto, never-empty floor, shared tally."""
from __future__ import annotations

from stackowl.infra.observability import log


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

"""Which of the platform's capabilities has nobody ever used?

ONE definition, asked by everyone who needs it. This started life inside
``learned_tool_loader`` on 2026-09-01 and answered the question for learned tools
only — which is the example, not the architecture. The same blindness covers the
whole presented set, and it hides more there:

* ``evolve_now`` holds a GUARANTEED presentation slot on every turn and has been
  invoked **zero times in the platform's entire recorded history**.
* ``synthesize_skills`` — likewise zero, all-time. (It already lost its
  guaranteed slot by Bakir's ESC-46 decision on 2026-08-23.)
* ``reflect_now`` — 11 invocations since 2026-06-19, so roughly never volunteered.

Meanwhile ``note_applied_lesson`` has 786 invocations, the most recent minutes
ago. THE CONTRAST IS THE FINDING, and it is not "the model ignores learning":
a tool that records something as a BYPRODUCT of answering gets used constantly,
while tools that ask the model to stop and do meta-work instead of finishing the
user's task are never chosen. That is an incentive shape, not a discoverability
bug, and presenting them harder would not change it.

NOTHING IS REMOVED HERE, deliberately. Zero invocations is not proof of
uselessness — a tool the presentation cap dropped was never OFFERED, and
``skills_list`` already cost this project that exact mistake. What was missing is
that the accumulation was INVISIBLE.

AN UNKNOWN MUST NOT READ AS "NONE": with no pool this reports UNKNOWN rather than
a silent zero, and a failed read reports nothing rather than a false zero.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.tenancy.principal import DEFAULT_PRINCIPAL_ID

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from collections.abc import Iterable

#: Every tool name ever recorded as invoked. ``tool_sequence`` is what the model
#: EMITTED, normalized at the store (see ``memory.outcome_store``), so a
#: well-formed name for a tool that does not exist can appear here — harmless for
#: this question, which only ever asks whether a REGISTERED name is absent.
#: OWNER-SCOPED. `task_outcomes` is owner-governed (migration 0043) and
#: tests/tenancy/test_no_owner_scope_bypass.py fails the build for an unscoped
#: statement on one. This shipped UNSCOPED on 2026-09-02 and the tripwire caught
#: it — a day later, because that item's test run covered tests/tools/meta and
#: tests/startup and not tests/tenancy. Second time that same gap in test
#: selection has let a cross-cutting tripwire go unrun.
#:
#: The scope changes the ANSWER, deliberately: a tool used only by another
#: principal now reads as never-invoked for this one. That is the correct
#: per-tenant view — reporting every principal's usage into one report is the
#: cross-tenant leak the tripwire exists to stop.
_USED_SQL = (
    "SELECT DISTINCT je.value AS tool FROM task_outcomes t, "
    "json_each(t.tool_sequence) je WHERE t.tool_sequence NOT IN ('', '[]') "
    "AND t.owner_id = ?"
)


async def report_never_invoked(
    names: Iterable[str], db: object | None, *, scope: str
) -> list[str]:
    """Log, at INFO, which of ``names`` has never been invoked. Never raises.

    Args:
        names: Registered tool names to check.
        db: Something with ``fetch_all``; ``None`` means usage is unknowable.
        scope: What this set is — e.g. ``"learned"`` or ``"all"`` — so two
            reports in the same boot are distinguishable.

    Returns:
        The never-invoked names, sorted. Empty when nothing could be determined,
        which callers must NOT read as "everything is used".
    """
    wanted = sorted({str(n) for n in names if str(n).strip()})
    if not wanted:
        return []
    if db is None:
        log.tool.info(
            "[tools] usage_report: usage UNKNOWN — no pool wired, so "
            "never-invoked tools cannot be reported",
            extra={"_fields": {"scope": scope, "n_registered": len(wanted)}},
        )
        return []
    try:
        rows = await db.fetch_all(_USED_SQL, (DEFAULT_PRINCIPAL_ID,))  # type: ignore[attr-defined]
        used = {str(r["tool"]) for r in rows}
    except Exception as exc:  # noqa: BLE001 — a report may never cost the boot
        log.tool.warning(
            "[tools] usage_report: could not read tool usage — not reporting, "
            "rather than reporting a false zero",
            exc_info=exc, extra={"_fields": {"scope": scope}},
        )
        return []
    # THE SAME RULE THIS MODULE EXISTS FOR, applied to its own answer. "An
    # UNKNOWN must not read as NONE" was enforced for a missing POOL and not for
    # missing DATA: with no recorded invocations at all, `used` is empty, every
    # registered name comes back "never invoked", and the line is shaped exactly
    # like a genuine finding. A numerator over a zero denominator — in the module
    # written to make numerators readable. Nothing is reported rather than
    # accusing the whole registry on no evidence.
    if not used:
        log.tool.info(
            "[tools] usage_report: usage UNKNOWN — no invocation history for "
            "this owner, so every registered name would read as never-invoked",
            extra={"_fields": {"scope": scope, "n_registered": len(wanted)}},
        )
        return []
    never = sorted(n for n in wanted if n not in used)
    log.tool.info(
        "[tools] usage_report: capability usage",
        extra={"_fields": {
            "scope": scope,
            "n_registered": len(wanted),
            "n_never_invoked": len(never),
            # THE DENOMINATOR. "15 of 79 never invoked" cannot be weighed without
            # knowing how much history it stands on, and its absence is why the
            # same list printed at every boot for days without ever settling the
            # question it was built to inform.
            "n_observed_tools": len(used),
            "never_invoked": never,
        }},
    )
    return never

"""A scheduled job's trace must name a channel, or consent has nothing to scope to.

MEASURED 2026-08-31: five refusals across the day, roughly one every one to three
hours, all from ``job:incident_escalation-9fb1c485``::

    tool_build.execute: no channel/session to scope consent — refused
    {"tool": "verify_shell_effect_before_retry", "interactive": false,
     "channel": null, "has_session": true}

and the same for ``delegate_task_scope_gate``,
``incident_delegate_task_unachieved_effect`` and ``owl_build_verify_before_claim``.
So every time the RCA concluded "build this tool", the build was refused. The
self-healing loop diagnosed correctly and then could not act — Bakir's
authority-versus-action shape, in the lane that exists to heal the platform.

TOOL_BUILD IS NOT THE DEFECT, and its own comment says so: "Not 'no human' — no
LANE at all, so there is nothing to scope a grant or an audit record to. That is a
WIRING FAULT, not an autonomy case." The measurement agrees: ``has_session`` is
true and ``channel`` is null. The session exists; the channel was never set.

``_bind_job_trace`` called ``TraceContext.start(session_key=...)`` and nothing
else, while ``start`` has accepted a ``channel`` all along.

"internal" IS NOT INVENTED HERE. ``sessions_spawn``, ``sessions_send`` and
``delegate_task`` already write ``str(ctx.get("channel") or "internal")`` — it is
the platform's existing name for a lane with no user channel, and reusing it keeps
one vocabulary instead of minting a second.

A JOB WITH A REAL TARGET USES IT. morning_brief carries ``["telegram"]``; its
consent scope should be telegram, not a placeholder. The placeholder is only for
maintenance work that genuinely has no address.
"""

from __future__ import annotations

from types import SimpleNamespace

from stackowl.infra.trace import TraceContext
from stackowl.scheduler.scheduler import _bind_job_trace


def _job(job_id: str = "j-1", **kw: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "job_id": job_id, "primary_channel": None, "target_channels": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_a_maintenance_job_still_gets_a_lane() -> None:
    """The case that was refusing every RCA-driven tool_build."""
    token = _bind_job_trace(_job())
    try:
        assert TraceContext.get().get("channel") == "internal", (
            "a scheduled job has no channel, so consent has nothing to scope to "
            "and tool_build refuses — this is the 2026-08-31 wiring fault"
        )
    finally:
        TraceContext.reset(token)


def test_a_job_with_a_real_channel_uses_it() -> None:
    """morning_brief's consent scope is telegram, not a placeholder."""
    token = _bind_job_trace(_job(primary_channel="telegram"))
    try:
        assert TraceContext.get().get("channel") == "telegram"
    finally:
        TraceContext.reset(token)


def test_target_channels_is_honoured_when_primary_is_absent() -> None:
    token = _bind_job_trace(_job(target_channels=["slack", "telegram"]))
    try:
        assert TraceContext.get().get("channel") == "slack"
    finally:
        TraceContext.reset(token)


def test_the_lane_identity_is_unchanged() -> None:
    """session_key still carries the job identity — the trace stays per-RUN."""
    token = _bind_job_trace(_job("morning_brief-abc"))
    try:
        assert TraceContext.get().get("session_key") == "job:morning_brief-abc"
    finally:
        TraceContext.reset(token)


def test_conversation_id_is_still_none() -> None:
    """Deliberate, and documented: "background work that never passed through
    ingress has a lane but no incarnation, and inventing one would attribute its
    cost to a conversation that never happened"."""
    token = _bind_job_trace(_job())
    try:
        assert TraceContext.get().get("conversation_id") is None
    finally:
        TraceContext.reset(token)


def test_a_job_that_cannot_describe_itself_still_gets_a_lane() -> None:
    """A handler-supplied object without the attributes must not crash dispatch."""
    token = _bind_job_trace(SimpleNamespace(job_id="odd"))
    try:
        assert TraceContext.get().get("channel") == "internal"
    finally:
        TraceContext.reset(token)


def test_the_job_is_still_not_interactive() -> None:
    """Giving it a lane must not make it look like a human is attached — that
    would invert consent the other way."""
    token = _bind_job_trace(_job())
    try:
        assert TraceContext.get().get("interactive") is False
    finally:
        TraceContext.reset(token)

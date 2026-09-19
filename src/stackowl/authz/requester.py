"""Who is asking, and what they are granted (Story 4.3, AD-1/AD-27).

TWO THINGS, DELIBERATELY MINIMAL. :func:`requester_kind_from_trace` reads the
ONE existing "who is driving this" signal (``TraceContext``'s ``principal``
ContextVar, set by ``ConsentPolicy``/the scheduler — see
``stackowl.tools.consent.PRINCIPAL_AUTONOMOUS_SCHEDULER``, a story-3.4 concept
this module reuses rather than duplicates) and reduces it to a
:data:`RequesterKind`. :func:`principal_for` maps that kind to a
:class:`~stackowl.authz.severity.ControlPrincipal`.

2026-09-19: :func:`principal_for` grants ``ALL_SEVERITIES`` to EVERY requester
kind. That is deliberate and documented, not a bug — the real per-requester-
kind/reversibility POLICY decision is Story 4.4's action-policy gate (AD-27);
this story only builds the structural funnel every command runs through
before a handler ever executes. Keeping the simplification in exactly ONE
function means 4.4 replaces one function, not N call sites that each learned
to skip the check.

This platform has exactly one human owner and no live owl/crew/voice actors
driving commands yet — ``owl``/``voice-unverified`` are declared in the
vocabulary for 4.4+ to start producing, never returned by
:func:`requester_kind_from_trace` today.
"""

from __future__ import annotations

from typing import Final, Literal

from stackowl.authz.severity import ALL_SEVERITIES, ControlPrincipal
from stackowl.infra.trace import TraceContext
from stackowl.tools.consent import PRINCIPAL_AUTONOMOUS_SCHEDULER

#: AD-1's closed requester-kind vocabulary: "owner, owl/crew, voice-unverified,
#: autonomous". Additive-only, mirrors every other closed vocabulary in this
#: tree (e.g. ``journal.enums.ActorKind``).
RequesterKind = Literal["owner", "owl", "voice-unverified", "autonomous"]

_CREDENTIAL_ID: Final = "trace-principal"


def requester_kind_from_trace() -> RequesterKind:
    """The requester kind for the CURRENT trace context — ingress provenance,
    never the caller's payload (AD-1).

    ``PRINCIPAL_AUTONOMOUS_SCHEDULER`` on ``TraceContext.get()["principal"]``
    (set by ``_bind_job_trace`` for every scheduler-driven trigger) maps to
    ``"autonomous"``; anything else — including the default, unset value —
    maps to ``"owner"``, since every live interactive surface today (Telegram,
    TUI, a slash command, an LLM tool call inside a user turn) is the one
    owner acting. ``owl``/``voice-unverified`` are declared in
    :data:`RequesterKind` for 4.4+ and never produced here.
    """
    principal = TraceContext.get().get("principal")
    if principal == PRINCIPAL_AUTONOMOUS_SCHEDULER:
        return "autonomous"
    return "owner"


def principal_for(requester_kind: RequesterKind) -> ControlPrincipal:
    """The :class:`ControlPrincipal` a requester kind is granted TODAY.

    ``principal_id`` is the *requester_kind* itself — distinct principals for
    distinct kinds, even though every one of them is granted the same
    ``ALL_SEVERITIES`` set today (a single hardcoded "owner" id would make an
    "autonomous"-driven refusal and an "owner"-driven refusal indistinguishable
    in a log line the moment 4.4 makes the grant actually differ by kind).

    2026-09-19: every requester kind is granted ``ALL_SEVERITIES`` — see this
    module's docstring. Story 4.4's action-policy gate is the ONE place that
    changes when the real per-requester-kind/reversibility policy lands.
    """
    return ControlPrincipal(
        principal_id=requester_kind,
        credential_id=f"{_CREDENTIAL_ID}:{requester_kind}",
        granted=ALL_SEVERITIES,
    )

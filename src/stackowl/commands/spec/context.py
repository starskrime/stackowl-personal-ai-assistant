"""``CommandContext``/``CommandOutcome`` -- what a handler receives, and what
it hands back (Story 4.3, AD-26).

``ActorKind`` (``journal/enums.py``)'s own docstring already named this:
"``CommandContext`` (the richer actor/device/requester-kind carrier) does not
exist yet -- Epic 4." This is that carrier. A subsystem mutator (e.g.
``JobScheduler.pause``) reads ``CommandContext.command_id`` for its own
idempotency-receipt guard and ``requester_kind`` to attribute the domain event
it alone records (``job.paused``) -- the COMMAND task wrapper
(``execute.py::execute_command_task``) records only ``command.*`` lifecycle
events, never the domain event (no double-recording, per this story's
Boundaries).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CommandContext(BaseModel):
    """Everything a handler needs to know about WHO asked and WHICH command
    this is, carried alongside the already-validated typed payload."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command_id: str = Field(min_length=1)
    command_type: str = Field(min_length=1)
    #: ``authz.requester.RequesterKind`` — kept as ``str`` here (not the
    #: Literal) so this module never imports ``authz.requester`` back for a
    #: type-only reference; the value always comes from that vocabulary.
    requester_kind: str
    #: Declared for voice (4.4+); carried, unused beyond storage, this story.
    utterance_id: str | None = None
    #: Story 4.6 — ``authz.action_policy.ActionPolicyDecision.
    #: authority_grant_id``, carried through so a handler that ran an
    #: irreversible command under standing authority can journal WHICH grant
    #: authorized it. ``None`` for every command this story (no live caller
    #: ever resolves a real one yet — see ``execute.py``'s own docstring).
    authority_grant_id: str | None = None


class CommandOutcome(BaseModel):
    """What a handler (or the execution wrapper) reports back."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    success: bool
    #: Small, JSON-shaped result a caller (e.g. the ``cronjob`` tool) reads to
    #: build its own honest, synchronous ``ToolResult`` — never free text.
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

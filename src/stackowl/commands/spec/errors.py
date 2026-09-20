"""Errors ``commands/spec/`` raises (Story 4.3, AD-1/AD-7).

Kept IN this package rather than in ``stackowl.exceptions`` — this package's
own AD-7 import boundary is "``stackowl.authz``, ``stackowl.pipeline.durable``,
stdlib and pydantic, never a subsystem", and ``stackowl.exceptions`` is none of
those. All three subclass a plain stdlib exception rather than ``DomainError``/
``SecurityError`` for the same reason: pulling those base classes in would be
exactly the cross-boundary import this module exists to avoid. Any caller
OUTSIDE ``commands/spec/`` (``task_loop_runner.py``, ``scheduler/commands.py``,
``cronjob.py``) is free to import these directly — nothing restricts them.
"""

from __future__ import annotations


class CommandTypeNotDeclaredError(ValueError):
    """No :class:`~stackowl.commands.spec.command_spec.CommandSpec` (and/or no
    handler) is registered for a command type. AD-1: there is no generic "run
    any command" fallback, so an unregistered type is refused loudly rather
    than silently no-op'd."""

    def __init__(self, command_type: str) -> None:
        self.command_type = command_type
        super().__init__(f"no CommandSpec is declared for {command_type!r}")


class CommandRefusedError(PermissionError):
    """A command's severity check (``authz.requester.principal_for``) refused
    it before its handler ran (AD-1: "no preview/dry-run returns before the
    severity check"). Not exercised by any live surface today —
    ``principal_for`` currently grants every severity to every requester kind
    (Story 4.4 is the real policy gate) — but a unit test that builds a
    restricted ``ControlPrincipal`` directly proves this is where the call
    site actually stops.
    """

    def __init__(self, command_type: str, severity: str, requester_kind: str) -> None:
        self.command_type = command_type
        self.severity = severity
        self.requester_kind = requester_kind
        super().__init__(
            f"{requester_kind!r} may not invoke {command_type!r} "
            f"(severity {severity!r})"
        )


class CommandNeedsDecisionError(Exception):
    """The action-policy gate (``authz.action_policy.decide``) decided this
    command may not run at once (Story 4.4, AD-27) -- a CONTROL SIGNAL, not a
    failure, mirroring ``pipeline.durable.addressing.NoAddresseeCompletion``'s
    own shape: raised by ``execute.py::execute_command_task`` immediately
    after a passing severity check, and caught by BOTH of this task kind's
    callers (``submit.py``'s inline path, ``loop.py``'s tick-driven
    ``_dispatch``) before their generic ``except Exception`` -- neither ever
    reports it as a handler failure. The catching caller owns the actual
    park-and-open-a-Needs-you-item I/O (``pipeline/durable/store.py::
    park_for_decision``), never this package (AD-7: ``commands/spec/`` never
    imports ``journal``).
    """

    def __init__(self, command_type: str, outcome: str, payload_summary: str) -> None:
        self.command_type = command_type
        self.outcome = outcome
        self.payload_summary = payload_summary
        super().__init__(
            f"{command_type!r} needs a decision before it may run "
            f"(outcome={outcome!r})"
        )

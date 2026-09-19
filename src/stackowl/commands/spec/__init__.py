"""``commands/spec/`` -- the ONE typed submit entry every state-changing
surface calls (Story 4.3, AD-1/AD-7/AD-26).

PLACEMENT: a new sub-package under ``commands/``, not under ``authz/`` or
``pipeline/durable/`` — the owner's 2026-09-13 placement vote (epic-4-context,
AD-7): "``commands/spec/`` holds the ``CommandSpec`` table and submit entry,
and depends only on ``authz/`` and ``pipeline/durable`` (tripwire-enforced);
the rest of ``commands/`` is the existing slash-command surface that submits
through that entry." Nesting it under ``authz/`` would have mixed "who may act"
(severities, the principal, the future action-policy gate) with "what a
command IS and how it runs" — two different questions AD-7 keeps apart on
purpose. Nesting it under ``pipeline/durable/`` would have made the durable
task loop own command DECLARATION as well as command EXECUTION, when the loop
itself must stay kind-agnostic (``pipeline/durable/loop.py``'s own docstring:
"the store owns state, the loop owns pacing"). ``commands/`` — the existing
slash-command surface's own package — is the natural home for "the rest of
the platform's command vocabulary", with this sub-package split out
specifically so its import boundary (``authz/`` + ``pipeline/durable`` only,
never a subsystem) can be tripwire-enforced without constraining the rest of
``commands/`` the same way.

WHAT LIVES HERE. :class:`~stackowl.commands.spec.command_spec.CommandSpec` (the
declaration: type, typed payload, severity, reversibility, undo type),
:class:`~stackowl.commands.spec.context.CommandContext`/``CommandOutcome`` (the
execution-time carrier and its result),
:class:`~stackowl.commands.spec.registry.CommandSpecRegistry` (closed,
explicit — never a dynamic dispatch table),
:class:`~stackowl.commands.spec.handlers.CommandHandlerRegistry` (ditto for
handlers), :func:`~stackowl.commands.spec.idempotency.record_command_execution`
(the AD-26 receipt guard a subsystem mutator writes into its OWN transaction),
:func:`~stackowl.commands.spec.submit.submit_command` (the one submit entry)
and :func:`~stackowl.commands.spec.execute.execute_command_task` (the
``TaskLoop`` runner's COMMAND-kind branch target).

WHAT NEVER LIVES HERE, per AD-1's Boundaries: a generic
``execute_command(command_type: str, **kwargs)`` entry point, ``eval``/``exec``,
or ``getattr``-based dynamic dispatch. The handler registry this package
builds is a closed dict, proven by a tripwire
(``tests/commands/spec/test_one_door_tripwires.py``).
"""

from __future__ import annotations

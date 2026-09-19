"""``CommandSpec`` -- one command's declared shape (Story 4.3, AD-1/AD-26).

A ``CommandSpec`` is the declaration a subsystem (e.g. ``scheduler/commands.py``)
registers once, at import time, for exactly one real-world action. It carries
everything ``execute_command_task`` needs to run that action deterministically:
its typed payload, its severity (so the severity check has something to ask
``authz.requester.principal_for`` about), and whether it is reversible (so a
future undo, Story 4.5, knows which command types even have one).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stackowl.authz.severity import ALL_SEVERITIES

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from stackowl.commands.spec.context import CommandContext, CommandOutcome

#: The same closed vocabulary ``authz.severity`` already owns — reused, never
#: redeclared (Story 4.1's own AST tripwire forbids a second copy).
Severity = str

#: A registered command's deterministic handler. No model call, ever (AD-26):
#: "Handlers live only in core's deterministic command-handler registry."
CommandHandler = Callable[[BaseModel, "CommandContext"], Awaitable["CommandOutcome"]]


class CommandSpec(BaseModel):
    """One command type's full declaration.

    ``reversible``/``undo_command_type`` mirror the exact "non-null iff
    reversible" rule Story 4.2's ``SlashCommand``/``ToolManifest`` declarations
    already use elsewhere in this codebase for a bounded-label pair — enforced
    here by :meth:`_undo_matches_reversible` rather than trusted at each call
    site.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    command_type: str = Field(min_length=1)
    #: The pydantic model ``submit_command`` validates the caller's payload
    #: dict against, and the JSON text on the row round-trips through.
    payload_model: type[BaseModel]
    severity: Severity
    reversible: bool
    #: Non-null iff ``reversible`` — validated below. DECLARED only this
    #: story; never invoked (Story 4.5's undo execution is out of scope here).
    undo_command_type: str | None = None

    @model_validator(mode="after")
    def _severity_is_declared(self) -> CommandSpec:
        if self.severity not in ALL_SEVERITIES:
            raise ValueError(
                f"CommandSpec({self.command_type!r}).severity={self.severity!r} "
                f"is not one of {sorted(ALL_SEVERITIES)}"
            )
        return self

    @model_validator(mode="after")
    def _undo_matches_reversible(self) -> CommandSpec:
        if self.reversible and not self.undo_command_type:
            raise ValueError(
                f"CommandSpec({self.command_type!r}) is reversible but declares "
                "no undo_command_type"
            )
        if not self.reversible and self.undo_command_type:
            raise ValueError(
                f"CommandSpec({self.command_type!r}) is not reversible but "
                f"declares undo_command_type={self.undo_command_type!r}"
            )
        return self

"""Attrs models for the standing-authority grant/revoke events, and their
registration (Story 4.6, FR32/FR33/NFR30/NFR31, AD-27).

One emitting process: ``authz`` -- ``authz/standing_authority.py``'s
``grant``/``revoke`` are the ONLY two functions that ever write the
``standing_authority`` table (NFR30: "writable only through authz/ APIs"),
and both record at the exact call site the state change commits (AD-24),
mirroring ``job_events.py``'s shape for the scheduler's own job lifecycle.
Importing this module registers both types as a side effect; ``journal/
__init__.py`` imports it for exactly that reason.

Reuses ``RecordKind.AUTHORITY`` (declared in ``journal/enums.py`` for this
story -- no existing member fits a standing-authority row) and
``table="standing_authority"`` so ``journal/coverage.py``'s tripwire sees the
new table this story's own migration (0153) adds as registry-covered, never
an unexplained gap.
"""

from __future__ import annotations

from typing import cast

from pydantic import Field

from stackowl.journal.enums import AttentionClass, RecordKind
from stackowl.journal.models import JournalAttrsBase
from stackowl.journal.registry import EventTypeSpec, get_registry

_EMITTING_PROCESS = "authz"
_TABLE = "standing_authority"

#: AD-4, verbatim: "attrs hold ids, numbers, closed enums and bounded labels
#: of at most 64 characters" -- mirrors every other journal attrs module's
#: own bound.
_MAX_LABEL_LEN = 64


class AuthorityGrantedAttrs(JournalAttrsBase):
    """``authority.granted`` -- ``authz.standing_authority.grant`` committed
    a new active standing-authority row."""

    scope_kind: str = Field(max_length=_MAX_LABEL_LEN)
    scope_id: str = Field(max_length=_MAX_LABEL_LEN)
    command_type: str = Field(max_length=_MAX_LABEL_LEN)
    granted_by: str = Field(max_length=_MAX_LABEL_LEN)


class AuthorityRevokedAttrs(JournalAttrsBase):
    """``authority.revoked`` -- ``authz.standing_authority.revoke`` closed an
    active standing-authority row."""

    scope_kind: str = Field(max_length=_MAX_LABEL_LEN)
    scope_id: str = Field(max_length=_MAX_LABEL_LEN)
    command_type: str = Field(max_length=_MAX_LABEL_LEN)
    revoked_by: str = Field(max_length=_MAX_LABEL_LEN)


def _narrate_granted(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001 -- `name` is the row id, no per-scope narrator exists yet, same precedent as command.enqueued/completed
    a = cast(AuthorityGrantedAttrs, attrs)
    return (
        f"Standing authority granted for {a.command_type} on "
        f"{a.scope_kind} {a.scope_id}."
    )


def _narrate_revoked(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001 -- see _narrate_granted
    a = cast(AuthorityRevokedAttrs, attrs)
    return (
        f"Standing authority revoked for {a.command_type} on "
        f"{a.scope_kind} {a.scope_id}."
    )


def _register() -> None:
    registry = get_registry()
    registry.register(EventTypeSpec(
        type="authority.granted", schema_version=1, attrs_model=AuthorityGrantedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.AUTHORITY,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_granted,
    ))
    registry.register(EventTypeSpec(
        type="authority.revoked", schema_version=1, attrs_model=AuthorityRevokedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.AUTHORITY,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_revoked,
    ))


_register()

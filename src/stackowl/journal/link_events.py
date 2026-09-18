"""``link.hello_mismatch_standdown`` -- the gateway↔core repeated-Hello-
mismatch stand-down, journaled for the first time (Story 3.1).

Before this story the stand-down was a single ``log.critical`` line right
before ``_supervise_core`` permanently stops watching core (Spec 2.3) -- a
give-up with no durable trace once that log line scrolls past. Registration-
only, mirroring ``budget_events.py``'s own shape: the emitting site
(``startup.orchestrator``) opens its own ``async with db_pool.transaction()``
block and calls ``journal.record()`` directly, right before the critical log
+ return -- with ``bypass_write_gate=True`` (the ONE sanctioned call site,
``recorder.py``'s own docstring): the write-gate is unconditionally paused at
that exact point (every Hello mismatch pauses it, and nothing resumes it
before 3 consecutive mismatches trigger this stand-down), so a bare call
would always be refused and silently defeat this story's own AC for this
trigger.

WHY ``table=None``. Like ``budget.warning``, this describes no single owning
row -- a signal over a COUNTER (``GatewayLink.consecutive_hello_mismatches``)
with no row of its own, not an aggregate over a table this time, but the same
"no owning row to point a record_ref at" reason
(``EventTypeSpec.table``'s own docstring).
"""

from __future__ import annotations

from typing import cast

from stackowl.journal.enums import AttentionClass, Intensity, NeedsYouKind, RecordKind
from stackowl.journal.models import JournalAttrsBase
from stackowl.journal.registry import EventTypeSpec, get_registry

_EMITTING_PROCESS = "startup.orchestrator"


class LinkHelloMismatchStanddownAttrs(JournalAttrsBase):
    """``link.hello_mismatch_standdown`` -- ``_supervise_core`` stopped
    respawning core because ``gateway_link.consecutive_hello_mismatches``
    reached ``_MAX_CONSECUTIVE_HELLO_MISMATCHES`` (Spec 2.3)."""

    consecutive_mismatches: int


def _narrate_standdown(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001 -- no resolver registered for RecordKind.LINK
    a = cast(LinkHelloMismatchStanddownAttrs, attrs)
    return (
        "The gateway stopped restarting core after "
        f"{a.consecutive_mismatches} consecutive Hello mismatches."
    )


def _register() -> None:
    get_registry().register(EventTypeSpec(
        type="link.hello_mismatch_standdown", schema_version=1,
        attrs_model=LinkHelloMismatchStanddownAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.LINK,
        attention_class=AttentionClass.NEEDS_YOU, intensity=Intensity.HIGH,
        needs_you_kind=NeedsYouKind.INCIDENT,
        # No single owning row -- a signal over a counter, not a table row
        # (spec Design Notes).
        table=None, narrate=_narrate_standdown,
    ))


_register()

"""Bakir: "Today when rsa comes and disappears."

He is right, and it is not a display quirk. Every incident alert this platform
sends is delivered through ``_build_health_alert_sink``, which sets::

    ephemeral=True,

and the deliverer's own docstring says exactly what that does: "when set AND a
concrete chat_id is available, the message is sent via ``send_ephemeral`` (silent,
muted) instead of ``send_text``, then best-effort DELETED once sent — so the probe
proves the real send path without leaving a visible message behind."

That behaviour is designed for a PROBE. The health canary uses it to prove the send
path works without leaving litter, and the sink's own comment justifies it for the
same reason: "Health degraded/recovered flaps are operator noise, not durable
content — they should not linger in the chat forever."

THE REASONING IS RIGHT FOR A FLAP AND WRONG FOR AN INCIDENT, and one sink serves
both — ``assembly.py`` passes the same ``health_alert`` to the health sweep (line
634) AND to ``IncidentEscalationHandler`` (line 685). So the twelve "RCA complete"
messages Bakir received on 2026-08-31 were sent silent, muted, and then deleted.
He could not read them, which is why the same operator both objected to the page
volume and asked for "incidents to be delivered to user": the ones he was getting
vanished before he could look.

A CONCLUDED RCA IS DURABLE CONTENT. It is the platform's account of something that
broke and what it decided about it. A flap is noise; a diagnosis is a record.
"""

from __future__ import annotations

import inspect

from stackowl.scheduler import assembly


def test_the_incident_handler_does_NOT_get_the_self_deleting_sink() -> None:
    """The defect, pinned at the wiring. One sink for a probe and for a diagnosis
    is how a durable message inherits a probe's disappearing act."""
    source = inspect.getsource(assembly)
    idx = source.find("IncidentEscalationHandler(")
    assert idx != -1, "the handler construction moved — update this test"
    block = source[idx : idx + 1800]
    assert "alert=health_alert," not in block, (
        "the incident handler is still using the ephemeral health-flap sink, so "
        "every RCA conclusion is sent muted and then deleted"
    )
    assert "alert=incident_alert," in block


def test_the_health_sweep_KEEPS_its_ephemeral_sink() -> None:
    """Unchanged, and deliberately: a degraded/recovered flap really is noise, and
    that reasoning is written down where it is used."""
    source = inspect.getsource(assembly)
    idx = source.find("HealthSweepHandler(")
    assert idx != -1
    assert "alert=health_alert," in source[idx : idx + 1200]


def test_both_sinks_come_from_ONE_builder() -> None:
    """Two builders would be two copies of "how an operator alert is addressed",
    and the address resolution is the fiddly half."""
    assert source_count(inspect.getsource(assembly), "def _build_health_alert_sink") == 1
    assert "ephemeral: bool" in inspect.getsource(assembly._build_health_alert_sink)


def test_the_durable_sink_is_not_ephemeral() -> None:
    """The whole point. Read off the builder rather than trusted."""
    src = inspect.getsource(assembly._build_health_alert_sink)
    assert "ephemeral=ephemeral," in src, (
        "the flag must be the builder's argument, not a literal — a literal is how "
        "one call site's decision became every call site's behaviour"
    )


def source_count(haystack: str, needle: str) -> int:
    return haystack.count(needle)

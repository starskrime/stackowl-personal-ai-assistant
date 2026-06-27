"""Shared DeliveryAck post-condition for the outbound send tools (ADR-1).

``send_message`` / ``send_file`` already self-stamp ``verified = status=='delivered'``
(F-30). This routes that same truth through the one AcceptanceAuthority: the transport's
``delivery_status`` (the deliverer's actual return, distinct from the success bool the
model sees) becomes a :class:`DeliveryAck` the authority observes. ``'delivered'`` is the
only ack; ``'batched'``/``'deferred'``/``'suppressed'`` are queued-not-delivered (not
acked); a record with no ``delivery_status`` (e.g. ``action='list'``) is no delivery at
all (``None`` — nothing to verify). Pure and total — never raises (a malformed output
yields ``None``, no opinion).
"""

from __future__ import annotations

import json

from stackowl.pipeline.acceptance_authority import DeliveryAck


def delivery_post_condition(output: str) -> DeliveryAck | None:
    """Build a :class:`DeliveryAck` from a send tool's JSON output record, or ``None``
    when the call delivered nothing. Never raises."""
    try:
        record = json.loads(output).get("record", {})
        status = record.get("delivery_status")
    except (ValueError, TypeError, AttributeError):
        return None
    if status is None:
        return None
    return DeliveryAck(acked=(status == "delivered"), channel=record.get("target"))

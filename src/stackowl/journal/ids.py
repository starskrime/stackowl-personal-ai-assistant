"""``event_id`` generation -- UUIDv7 text, from one place (AC)."""

from __future__ import annotations

import uuid_utils


def new_event_id() -> str:
    """A UUIDv7 string: time-ordered, so ``event_id`` sorts the same way
    ``cursor`` does even though the two are generated independently."""
    return str(uuid_utils.uuid7())

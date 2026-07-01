"""FR-5 tripwire — reflection_writer must never defer under load.

A one-line property flip is easy to silently revert; this pins the value so a
future edit that flips it back to True gets caught rather than silently
reintroducing the chronic 15-min-cadence slippage FR-5 fixed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from stackowl.memory.reflection_writer_handler import ReflectionWriterHandler


def test_reflection_writer_never_defers_under_load() -> None:
    handler = ReflectionWriterHandler(
        db=MagicMock(), provider_registry=MagicMock(), embedding_registry=MagicMock(),
    )
    assert handler.defer_under_load is False

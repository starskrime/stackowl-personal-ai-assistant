"""Publishing a channel that is already published is the ordinary case, not an alarm.

MEASURED 2026-09-09 across 13 retained log files: **82** `[channel_registry] register:
duplicate` warnings, EVERY ONE `channel: telegram`, roughly one per boot — in the
operator's alarm channel, on an entirely normal path.

TWO PUBLISHERS WANTED THE SAME THING AND ONLY ONE ASKED FOR IT. A socket proxy has to be
in the core's `ChannelRegistry` or proactive sends fail "unknown channel", and two places
put it there: `channels/socket_adapter.py` at boot, and the core ingress loop in
`startup/orchestrator.py` when a channel first appears. The first asked `get()` and
skipped when present. The second wrote `contextlib.suppress(Exception)` around a bare
`register()` and called itself "idempotent — guarded by `registered`", a set that guards
its own call site and knows nothing about the boot-time registration into the same
singleton.

AND THE BROAD SUPPRESS HID MORE THAN THE DUPLICATE. `suppress(Exception)` swallows a
GENUINE registration failure just as quietly, and that failure is silent by nature —
proactive sends to the channel simply stop resolving, and nothing says why.

`ChannelRegistry.ensure_registered` is now the one place that answers "make sure this
channel resolves", and both publishers ask it. `register` is unchanged: for its own
contract a duplicate IS a caller error and still raises.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.channels.registry import ChannelRegistry
from stackowl.exceptions import ChannelAlreadyRegisteredError


class _FakeAdapter:
    """Minimal adapter — the registry only ever reads `channel_name`."""

    def __init__(self, name: str = "telegram") -> None:
        self.channel_name = name


class _Capture(logging.Handler):
    """Records straight off the named logger.

    NOT `caplog`: `configure_logging` sets `propagate = False` on the `stackowl`
    logger, so a propagation-based fixture passes alone and fails in any session
    that configured logging first. DEBT-267 is the record of that.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _watch_gateway():
    logger = logging.getLogger("stackowl.gateway")
    handler = _Capture()
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return logger, handler, previous


def _duplicate_warnings(handler: _Capture) -> list[logging.LogRecord]:
    return [
        r for r in handler.records
        if "register: duplicate" in r.getMessage() and r.levelno >= logging.WARNING
    ]


@pytest.mark.tripwire
def test_publishing_the_same_channel_twice_raises_no_alarm() -> None:
    """THE REGRESSION, at the shape that produced all 82."""
    registry = ChannelRegistry()
    logger, handler, previous = _watch_gateway()
    try:
        assert registry.ensure_registered(_FakeAdapter()) is True, "first publish must register"
        assert registry.ensure_registered(_FakeAdapter()) is False, "second must be a no-op"
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)

    assert not _duplicate_warnings(handler), (
        "publishing an already-published channel still warns — that is the operator's "
        f"alarm channel on the normal path: {[r.getMessage() for r in handler.records]}"
    )
    assert registry.get("telegram") is not None, "the channel must still resolve"


def test_the_test_can_see_the_warning_it_asserts_is_absent() -> None:
    """VACUITY CONTROL. The assertion above passes trivially if this handler never
    catches anything. `register` still raises AND warns on a duplicate — that is its
    contract and it is deliberately unchanged — so it is the positive control."""
    registry = ChannelRegistry()
    registry.register(_FakeAdapter())
    logger, handler, previous = _watch_gateway()
    try:
        with pytest.raises(ChannelAlreadyRegisteredError):
            registry.register(_FakeAdapter())
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)

    assert _duplicate_warnings(handler), (
        "the positive control saw nothing, so the absence asserted above proves nothing"
    )


@pytest.mark.tripwire
def test_a_real_registration_failure_is_reported_not_swallowed() -> None:
    """The half `suppress(Exception)` got wrong. A channel that fails to publish stops
    resolving for proactive sends, and nothing else would ever say so."""
    registry = ChannelRegistry()

    def _boom(_adapter: object, source_name: str | None = None) -> None:
        raise RuntimeError("registry is wedged")

    registry.register = _boom  # type: ignore[method-assign]
    logger, handler, previous = _watch_gateway()
    try:
        assert registry.ensure_registered(_FakeAdapter()) is False
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)

    loud = [
        r for r in handler.records
        if r.levelno >= logging.WARNING and "registration FAILED" in r.getMessage()
    ]
    assert loud, (
        "a genuine registration failure was swallowed — the exact thing "
        f"suppress(Exception) did: {[r.getMessage()[:70] for r in handler.records]}"
    )


def test_ensure_registered_never_raises_whatever_the_registry_does() -> None:
    """A publisher must never be blocked by the bookkeeping it is doing on the side."""
    registry = ChannelRegistry()

    def _boom(_adapter: object, source_name: str | None = None) -> None:
        raise RuntimeError("registry is wedged")

    registry.register = _boom  # type: ignore[method-assign]
    assert registry.ensure_registered(_FakeAdapter()) is False

"""TestModeGuard — blocks live I/O when STACKOWL_TEST_MODE=1."""

from __future__ import annotations

import logging

from stackowl.exceptions import StackOwlError

log = logging.getLogger("stackowl.config")


class TestModeViolation(StackOwlError):
    """Raised when live I/O is attempted while test mode is active."""

    def __init__(self, operation: str) -> None:
        super().__init__(f"Live I/O blocked in test mode: {operation!r}")
        self.operation = operation


class TestModeGuard:
    """Class-level flag that gates all live I/O during tests.

    Activated automatically when ``Settings.test_mode`` is ``True``.
    Call ``TestModeGuard.assert_not_test_mode(op)`` at the boundary of
    any provider call, DB write, or channel send.
    """

    _active: bool = False

    @classmethod
    def activate(cls) -> None:
        cls._active = True
        log.warning("[config] TEST MODE ACTIVE — all live I/O must be intercepted")

    @classmethod
    def deactivate(cls) -> None:
        cls._active = False

    @classmethod
    def is_active(cls) -> bool:
        return cls._active

    @classmethod
    def assert_not_test_mode(cls, operation: str) -> None:
        """Raise TestModeViolation if test mode is active."""
        if cls._active:
            raise TestModeViolation(operation)

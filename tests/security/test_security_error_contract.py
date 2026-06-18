"""Verify SecurityError always triggers all three side effects: CRITICAL log, audit entry, toast."""

from __future__ import annotations

import logging

import pytest

from stackowl.exceptions import SecurityError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raise_and_catch(exc: type[SecurityError] = SecurityError, **kwargs: object) -> None:
    """Raise *exc* with optional kwargs and swallow it."""
    try:
        raise SecurityError(**kwargs) if exc is SecurityError else exc(**kwargs)  # type: ignore[arg-type]
    except SecurityError:
        pass


# ---------------------------------------------------------------------------
# Side-effect 1: CRITICAL log
# ---------------------------------------------------------------------------

def test_security_error_logs_critical(caplog: pytest.LogCaptureFixture) -> None:
    """SecurityError.__init__ must emit a CRITICAL record on stackowl.security."""
    with caplog.at_level(logging.CRITICAL, logger="stackowl.security"):
        _raise_and_catch(message="test_critical_log")
    assert any(
        r.levelno == logging.CRITICAL and "stackowl.security" in r.name
        for r in caplog.records
    ), f"Expected CRITICAL on stackowl.security; got: {[(r.name, r.levelname) for r in caplog.records]}"


def test_security_error_log_contains_category(caplog: pytest.LogCaptureFixture) -> None:
    """The CRITICAL log record fields must include the category."""
    with caplog.at_level(logging.CRITICAL, logger="stackowl.security"):
        _raise_and_catch(message="cat_test", category="path_traversal")
    crits = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert crits, "No CRITICAL records found"
    fields = getattr(crits[0], "_fields", {})
    assert fields.get("category") == "path_traversal"


# ---------------------------------------------------------------------------
# Side-effect 2: audit callback
# ---------------------------------------------------------------------------

def test_security_error_calls_audit_fn() -> None:
    """SecurityError must call _audit_fn with event_type='security_violation'."""
    calls: list[str] = []

    SecurityError.register_side_effects(
        audit_fn=lambda event, details: calls.append(event),
        notify_fn=None,
    )
    try:
        _raise_and_catch(message="audit_test")
    finally:
        SecurityError.register_side_effects(audit_fn=None, notify_fn=None)

    assert "security_violation" in calls, f"audit_fn not called with 'security_violation'; got {calls}"


def test_security_error_audit_fn_receives_category() -> None:
    """The audit callback dict must contain the category key."""
    received: list[dict[str, object]] = []

    SecurityError.register_side_effects(
        audit_fn=lambda event, details: received.append(details),
        notify_fn=None,
    )
    try:
        _raise_and_catch(message="cat_audit", category="policy_breach")
    finally:
        SecurityError.register_side_effects(audit_fn=None, notify_fn=None)

    assert received, "audit_fn was not called"
    assert received[0].get("category") == "policy_breach"


# ---------------------------------------------------------------------------
# Side-effect 3: toast notification callback
# ---------------------------------------------------------------------------

def test_security_error_calls_notify_fn() -> None:
    """SecurityError must call _notify_fn with a message containing the error text."""
    notifications: list[str] = []

    SecurityError.register_side_effects(
        audit_fn=None,
        notify_fn=lambda msg: notifications.append(msg),
    )
    try:
        _raise_and_catch(message="test_notify_message")
    finally:
        SecurityError.register_side_effects(audit_fn=None, notify_fn=None)

    assert any(
        "test_notify_message" in n for n in notifications
    ), f"notify_fn not called with expected message; got {notifications}"


def test_security_error_both_callbacks_fire() -> None:
    """Both audit and notify callbacks must fire on the same raise."""
    audit_calls: list[str] = []
    notify_calls: list[str] = []

    SecurityError.register_side_effects(
        audit_fn=lambda e, d: audit_calls.append(e),
        notify_fn=lambda m: notify_calls.append(m),
    )
    try:
        _raise_and_catch(message="dual_test")
    finally:
        SecurityError.register_side_effects(audit_fn=None, notify_fn=None)

    assert audit_calls, "audit_fn not called"
    assert notify_calls, "notify_fn not called"


# ---------------------------------------------------------------------------
# Category field
# ---------------------------------------------------------------------------

def test_security_error_default_category() -> None:
    """SecurityError.category must default to 'nfr33'."""
    try:
        raise SecurityError("default_cat")
    except SecurityError as exc:
        assert exc.category == "nfr33"


def test_security_error_custom_category() -> None:
    """SecurityError.category must reflect the provided value."""
    try:
        raise SecurityError("custom_cat", category="path_traversal")
    except SecurityError as exc:
        assert exc.category == "path_traversal"


# ---------------------------------------------------------------------------
# Context field
# ---------------------------------------------------------------------------

def test_security_error_context_defaults_to_empty_dict() -> None:
    """SecurityError.context must default to an empty dict."""
    try:
        raise SecurityError("ctx_default")
    except SecurityError as exc:
        assert exc.context == {}


def test_security_error_context_stored() -> None:
    """SecurityError.context must store the provided context dict."""
    ctx = {"file": "/etc/passwd", "user": "anonymous"}
    try:
        raise SecurityError("ctx_stored", context=ctx)
    except SecurityError as exc:
        assert exc.context == ctx

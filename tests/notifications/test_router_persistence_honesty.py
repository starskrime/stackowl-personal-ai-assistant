"""STEER-6 (F112) — the router's persistence docstring matches reality.

F112: the module docstring claimed "The raw message content is never persisted —
only sha256(message)[:16]", but the ``batched`` branch DOES persist the raw body
into ``notification_queue`` (the digest reads it back to transport). This guard
pins the corrected, honest contract so the false claim cannot silently return:

  * the module docstring no longer asserts the body is NEVER persisted;
  * it documents that batched bodies ARE persisted, with retention/cleanup.

The real persist-then-cleanup behaviour is exercised end-to-end by
``test_batched_transport.test_batched_persists_body_then_flush_transports``; this
test guards the DOCUMENTED contract (the F112 finding was a docstring lie).
"""

from __future__ import annotations

import stackowl.notifications.router as router_mod


def test_module_docstring_does_not_claim_body_never_persisted() -> None:
    doc = (router_mod.__doc__ or "").lower()
    assert doc, "router module must keep a docstring"
    # The false F112 claim must be gone.
    assert "never persisted" not in doc
    # The honest contract must be present: batched bodies ARE persisted...
    assert "persist" in doc
    assert "batched" in doc
    # ...and cleaned up (bounded retention, not indefinite).
    assert "retention" in doc or "cleaned up" in doc or "deletes" in doc

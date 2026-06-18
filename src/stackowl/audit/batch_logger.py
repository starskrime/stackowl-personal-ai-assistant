"""BatchAuditLogger — aggregate per-step events into one chained audit row.

When the ``browser_browse`` meta-tool runs an inner LLM loop with N steps, we
want one audit entry per *browse invocation* (with a ``steps`` array), not N
chained rows. This keeps the audit chain compact while preserving forensic
visibility into what the inner loop did.

Usage::

    batch = BatchAuditLogger(audit_logger=services.audit_logger, actor="scout",
                             event_type="browser_browse", target=url)
    with batch:
        batch.add_step({"action": "navigate", "url": "..."})
        batch.add_step({"action": "click", "index": 4})
        ...
    # on __exit__, one audit row is appended with details={"steps": [...], ...}
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import Any

log = logging.getLogger("stackowl.audit")


class BatchAuditLogger:
    """Context manager that buffers child events and commits one chained row."""

    def __init__(
        self,
        audit_logger: Any | None,
        *,
        event_type: str,
        actor: str,
        target: str | None,
        extra_details: dict[str, Any] | None = None,
    ) -> None:
        self._audit = audit_logger
        self._event_type = event_type
        self._actor = actor
        self._target = target
        self._extra = dict(extra_details) if extra_details else {}
        self._steps: list[dict[str, Any]] = []
        self._committed = False

    def add_step(self, step: dict[str, Any]) -> None:
        """Buffer one inner-loop step. Safe to call after __exit__ (no-ops)."""
        if self._committed:
            return
        self._steps.append(dict(step))

    def __enter__(self) -> BatchAuditLogger:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._committed:
            return
        self._committed = True
        if self._audit is None:
            log.debug(
                "[audit] batch_logger.exit: no audit_logger — skip commit",
                extra={"_fields": {"event_type": self._event_type, "steps": len(self._steps)}},
            )
            return
        details = {
            **self._extra,
            "steps": self._steps,
            "step_count": len(self._steps),
            "exception": None if exc is None else f"{type(exc).__name__}: {exc}",
        }
        try:
            self._audit.append(
                event_type=self._event_type,
                actor=self._actor,
                target=self._target,
                details=details,
            )
        except Exception as commit_exc:
            log.error(
                "[audit] batch_logger.exit: append failed",
                exc_info=commit_exc,
                extra={"_fields": {"event_type": self._event_type, "actor": self._actor}},
            )

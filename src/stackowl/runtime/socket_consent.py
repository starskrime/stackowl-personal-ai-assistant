"""SocketConsentPrompter — core-side consent over the gateway/core socket.

In the split the pipeline runs in the CORE but the real consent UI (Telegram
inline buttons, the TUI prompt) lives on the durable GATEWAY. This prompter
satisfies the :class:`~stackowl.tools.consent.ConsentPrompter` protocol
(``async def prompt(req) -> ConsentScope``) by serialising the request to a
``ConsentRequestFrame``, blocking on a per-request future, and resolving it when
the gateway returns a ``ConsentResponseFrame``. One instance serves every channel
(the request carries ``req.channel``; correlation is by ``consent_id``).

Fail-closed: any error, timeout, or unparseable decision yields ``DENY`` — a
consequential action is never run on an unanswered consent.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from stackowl.infra.observability import log
from stackowl.ipc.connection import FrameConnection
from stackowl.ipc.frames import ConsentRequestFrame
from stackowl.tools.consent import ConsentRequest, ConsentScope

_DEFAULT_TIMEOUT_SECONDS = 120.0


class SocketConsentPrompter:
    """ConsentPrompter that round-trips the decision over the socket."""

    def __init__(
        self, conn: FrameConnection, *, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self._conn = conn
        self._timeout = timeout_seconds
        self._pending: dict[str, asyncio.Future[ConsentScope]] = {}

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        consent_id = uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ConsentScope] = loop.create_future()
        self._pending[consent_id] = future
        log.gateway.info(
            "[ipc] socket consent: requesting decision from gateway",
            extra={"_fields": {
                "consent_id": consent_id, "channel": req.channel, "tool": req.tool_name,
            }},
        )
        try:
            await self._conn.send(
                ConsentRequestFrame(
                    consent_id=consent_id,
                    channel=req.channel,
                    tool_name=req.tool_name,
                    session_id=req.session_id,
                    category=req.category,
                    summary=req.summary,
                    allow_relaxation=req.allow_relaxation,
                )
            )
            return await asyncio.wait_for(future, timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 — consent fails CLOSED on any error
            log.gateway.warning(
                "[ipc] socket consent: no decision — denying",
                extra={"_fields": {"consent_id": consent_id, "error": str(exc)}},
            )
            return ConsentScope.DENY
        finally:
            self._pending.pop(consent_id, None)

    def resolve(self, consent_id: str, scope_value: str) -> None:
        """Resolve a pending request from an inbound ConsentResponseFrame."""
        future = self._pending.get(consent_id)
        if future is None or future.done():
            return
        try:
            scope = ConsentScope(scope_value)
        except ValueError:
            scope = ConsentScope.DENY
        future.set_result(scope)

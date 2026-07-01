"""ProactiveDeliverer — the outbound transport bridge (E7-S0).

The :class:`NotificationRouter` is a pure decision/audit component: it decides
``delivered`` / ``batched`` / ``suppressed`` and writes the audit row, but never
touches a channel adapter. This module is the missing bridge — it asks the
router for a decision and, only on ``delivered``, resolves the target channel
adapter and transports the message verbatim via ``send_text``.

Design (kept thin — this is StackOwl glue, not a vendor port):

* ``batched`` / ``suppressed`` decisions are already handled by the router
  (queued / logged) — the deliverer returns them untouched, no send.
* Self-healing ([[feedback_always_self_healing]]): an unknown channel or a
  failing ``send_text`` is caught, logged at ``error`` (B5), and surfaced as a
  terminal ``"failed"`` status — :meth:`deliver` NEVER raises into its caller.
  A single bounded retry covers a transient send error before failing.
* The deliverer emits NO new user-facing text — it transports the router-vetted
  message body verbatim.
"""

from __future__ import annotations

import time as _time
from typing import TYPE_CHECKING, Literal, Protocol, cast

from stackowl.infra.observability import log
from stackowl.notifications.router import DeliveryStatus
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.channels.registry import ChannelRegistry
    from stackowl.config.settings import Settings
    from stackowl.notifications.router import Notification, NotificationRouter
    from stackowl.notifications.undelivered_outbox import UndeliveredOutbox


class _TargetedSender(Protocol):
    """An adapter whose ``send_text`` accepts an explicit destination ``chat_id``.

    The base :class:`ChannelAdapter.send_text` is text-only; only telegram's
    override takes ``*, chat_id`` (the per-message target). When the deliverer
    has a concrete ``chat_id`` it narrows the resolved adapter to this Protocol
    so the typed call is exact — at runtime an explicit ``chat_id`` is only ever
    produced for a chat-addressable (telegram) channel.
    """

    async def send_text(self, text: str, *, chat_id: str | int | None = ...) -> None: ...


class _TargetedFileSender(Protocol):
    """An adapter whose ``send_file`` accepts an explicit destination ``chat_id``.

    Mirrors :class:`_TargetedSender` for the file path: the base
    :class:`ChannelAdapter.send_file` takes only ``(file_path, caption)``; only
    telegram's override adds ``*, chat_id``. When the deliverer has a concrete
    target it narrows the resolved adapter to this Protocol so the file upload
    reaches THAT chat instead of the adapter's shared ``_last_chat_id``.
    """

    async def send_file(
        self, file_path: str, caption: str | None = ..., *, chat_id: str | int | None = ...
    ) -> None: ...


# Urgency an agent-originated notification is permitted to request. ``critical``
# is reserved for user / job-config / system origin and is clamped down to
# ``normal`` for agent callers (S2 heartbeat_respond, S3 send_message).
AgentUrgency = Literal["normal", "low"]


def clamp_agent_urgency(requested: str) -> AgentUrgency:
    """Clamp an agent-requested urgency to the agent-permitted set.

    ``normal`` / ``low`` pass through unchanged; anything else (notably
    ``critical``) is clamped to ``normal``. Pure function — no clock, no I/O.
    System callers (e.g. ``/urgent``) do NOT use this clamp and keep their
    ability to send ``critical``.
    """
    if requested == "low":
        return "low"
    return "normal"


class ProactiveDeliverer:
    """Transports a router-vetted notification to its channel adapter.

    Holds the (decision) router and the (transport) channel registry. The
    registry singleton is resolved once at construction (in assembly) and
    injected — :meth:`deliver` never reaches for the singleton itself.
    """

    def __init__(
        self,
        router: NotificationRouter,
        registry: ChannelRegistry,
        settings: Settings,
        outbox: UndeliveredOutbox | None = None,
    ) -> None:
        self._router = router
        self._registry = registry
        self._settings = settings
        # PA5(b) — the durable NACK store. None keeps every existing test/
        # construction site byte-identical (no silent-drop persistence, same as
        # today); wired for real at assembly time (notifications/assembly.py).
        self._outbox = outbox

    async def deliver(self, notification: Notification) -> DeliveryStatus:
        """Route + transport ``notification``; never raises.

        Returns the router decision verbatim for ``batched`` / ``suppressed``
        (the router already queued / logged those), the router's ``delivered``
        on a successful ``send_text``, or ``"failed"`` if transport could not
        complete (unknown channel / adapter error after one retry).
        """
        # 1. ENTRY
        log.notifications.debug(
            "[notifications] deliverer.deliver: entry",
            extra={
                "_fields": {
                    "urgency": notification.urgency,
                    "category": notification.category,
                    "channel": notification.channel_name,
                    "has_file": notification.file_path is not None,
                }
            },
        )
        t0 = _time.monotonic()

        status = await self._router.deliver(notification)
        channel = notification.channel_name or self._settings.notifications.default_channel

        # 2. DECISION — only a ``delivered`` decision triggers transport.
        if status != "delivered":
            log.notifications.debug(
                "[notifications] deliverer.deliver: no transport (router-handled)",
                extra={"_fields": {"status": status, "channel": channel}},
            )
            self._log_exit(status, channel, t0)
            return status

        # 3. STEP — resolve adapter + transport. A file notification routes to the
        # adapter's send_file (caption == the router-vetted message body); the pure
        # text path is unchanged when file_path is None.
        if notification.file_path is not None:
            result = await self._transport_file(
                channel,
                notification.file_path,
                notification.message,
                chat_id=notification.target_chat_id,
            )
        else:
            # Thread the notification's explicit recipient (when the proactive
            # source could resolve one) through to ``send_text(chat_id=...)`` so
            # the message reaches THAT chat — not the adapter's shared mutable
            # ``_last_chat_id`` (which a newer inbound update could have pointed at
            # a different chat). ``None`` keeps the back-compat ``_last_chat_id``
            # fallback for text-only / single-terminal channels.
            result = await self._transport(
                channel, notification.message, chat_id=notification.target_chat_id
            )
        # ADR-2 — a FAILED transport is not surrendered until the RecoveryActuator's
        # reroute rung is exhausted: when an opt-in fallback channel is configured, the
        # message is rerouted there before delivery reports failure (F-64/65/66).
        result = await self._maybe_reroute(channel, notification, result)
        # PA5(b) — a terminal FAILED transport (retry + reroute both exhausted)
        # is a silent drop today: the body is gone, only the status is returned.
        # ADDITIVE: persist the durable NACK; never changes control flow/return.
        if result == "failed" and self._outbox is not None:
            await self._outbox.record_undelivered(
                identity_key=(
                    str(notification.target)
                    if notification.target is not None
                    else DEFAULT_PRINCIPAL_ID
                ),
                body=notification.message,
                reason="transport_failed",
                channel=channel,
                category=notification.category,
                urgency=notification.urgency,
                job_id=notification.job_id,
            )
        self._log_exit(result, channel, t0)
        return result

    async def _maybe_reroute(
        self, failed_channel: str, notification: Notification, status: DeliveryStatus
    ) -> DeliveryStatus:
        """ADR-2 — on a FAILED transport, hand the failure to the one RecoveryActuator,
        which runs a reroute rung to the configured ``notifications.fallback_channel`` and
        re-verifies it (a ``"delivered"`` status). Opt-in: an empty fallback (the default)
        ⇒ no reroute (byte-identical); a fallback equal to the channel that just failed is
        skipped. Never raises — any internal error leaves the original ``status``."""
        try:
            if status != "failed":
                return status
            fallback = self._settings.notifications.fallback_channel
            if not fallback or fallback == failed_channel:
                return status
            from stackowl.pipeline.recovery_actuator import Failure, RecoveryActuator

            async def _reroute() -> DeliveryStatus:
                if notification.file_path is not None:
                    return await self._transport_file(
                        fallback, notification.file_path, notification.message,
                        chat_id=notification.target_chat_id,
                    )
                return await self._transport(
                    fallback, notification.message, chat_id=notification.target_chat_id
                )

            failure = Failure(
                name=f"channel:{failed_channel}", kind="delivery", transient=True
            )
            outcome = await RecoveryActuator().recover(
                failure, reroute=_reroute, verify=lambda r: r == "delivered", record=False,
            )
            if outcome.recovered:
                log.notifications.info(
                    "[notifications] deliverer: primary channel failed — rerouted to fallback",
                    extra={"_fields": {
                        "failed_channel": failed_channel, "fallback_channel": fallback,
                    }},
                )
                return "delivered"
            return status
        except Exception as exc:  # B5 — reroute must never break delivery
            log.notifications.error(
                "[notifications] deliverer._maybe_reroute: failed — leaving original status",
                exc_info=exc,
                extra={"_fields": {"failed_channel": failed_channel}},
            )
            return status

    async def transport(self, channel: str, message: str) -> DeliveryStatus:
        """Transport an already-decided message body to ``channel``.

        Used by the digest flush, where the routing decision was made when the
        notification was first batched — re-deciding here would be wrong. Same
        self-healing contract as :meth:`deliver`: never raises; ``"failed"`` on
        unknown channel or a send that fails after one retry.
        """
        log.notifications.debug(
            "[notifications] deliverer.transport: entry",
            extra={"_fields": {"channel": channel}},
        )
        return await self._transport(channel, message, chat_id=None)

    async def _transport(
        self, channel: str, message: str, *, chat_id: str | int | None = None
    ) -> DeliveryStatus:
        """Resolve the adapter and send ``message``; retry-once on send error.

        ``chat_id`` is the EXPLICIT destination for this notification — under
        concurrency a bare proactive send would target the adapter's shared
        mutable ``_last_chat_id`` and could deliver to the wrong chat. When a
        concrete ``chat_id`` is supplied it is passed through as a keyword to
        ``send_text`` so the message reaches THAT chat. When ``None`` (the
        current proactive path — the ``Notification`` record carries no
        recipient), the ``chat_id`` kwarg is OMITTED entirely so that
        text-only adapters (cli/slack/discord/whatsapp, whose ``send_text``
        takes no ``chat_id``) keep working and telegram falls back to its
        ``_last_chat_id`` (back-compat).

        Returns ``"delivered"`` on success or ``"failed"`` (logged) on an
        unknown channel or a send that still fails after one retry. Never raises.
        """
        try:
            adapter = self._registry.get(channel)
        except Exception as exc:  # B5 — unknown / unavailable channel
            log.notifications.error(
                "[notifications] deliverer._transport: channel unavailable",
                exc_info=exc,
                extra={"_fields": {"channel": channel}},
            )
            return "failed"

        for attempt in (1, 2):
            try:
                # An explicit target is threaded as a kwarg; ``None`` omits the
                # kwarg so text-only adapters (no ``chat_id`` param) still accept
                # the call and telegram falls back to its ``_last_chat_id``.
                if chat_id is not None:
                    await cast("_TargetedSender", adapter).send_text(
                        message, chat_id=chat_id
                    )
                else:
                    await adapter.send_text(message)
                log.notifications.debug(
                    "[notifications] deliverer._transport: sent",
                    extra={
                        "_fields": {
                            "channel": channel,
                            "attempt": attempt,
                            "explicit_target": chat_id is not None,
                        }
                    },
                )
                return "delivered"
            except Exception as exc:  # B5 — transient/permanent send failure
                if attempt == 1:
                    log.notifications.warning(
                        "[notifications] deliverer._transport: send failed — retrying once",
                        exc_info=exc,
                        extra={"_fields": {"channel": channel, "attempt": attempt}},
                    )
                    continue
                log.notifications.error(
                    "[notifications] deliverer._transport: send failed after retry",
                    exc_info=exc,
                    extra={"_fields": {"channel": channel, "attempt": attempt}},
                )
                return "failed"
        return "failed"  # pragma: no cover — loop always returns

    async def _transport_file(
        self, channel: str, file_path: str, caption: str, *, chat_id: str | int | None = None
    ) -> DeliveryStatus:
        """Resolve the adapter and upload ``file_path`` via ``send_file``.

        ``chat_id`` is the EXPLICIT recipient for this file (the proactive source
        resolved it from the originating session). When supplied it is threaded as
        a keyword to ``send_file`` so the file reaches THAT chat rather than the
        adapter's shared mutable ``_last_chat_id``. When ``None`` the kwarg is
        OMITTED so text-only/file-capable adapters whose ``send_file`` takes no
        ``chat_id`` keep working and telegram falls back to ``_last_chat_id``.

        Returns ``"delivered"`` on a successful upload, or ``"failed"`` (logged,
        B5) on an unknown channel, a channel that does not support file send
        (``NotImplementedError``), or any send error. Never raises.

        Unlike :meth:`_transport`, a file upload is NOT retried: re-running an
        upload that may have partially succeeded risks a duplicate send, so a
        single attempt is made and any failure is surfaced structured.
        """
        try:
            adapter = self._registry.get(channel)
        except Exception as exc:  # B5 — unknown / unavailable channel
            log.notifications.error(
                "[notifications] deliverer._transport_file: channel unavailable",
                exc_info=exc,
                extra={"_fields": {"channel": channel}},
            )
            return "failed"

        caption_arg = caption or None
        try:
            # An explicit target is threaded as a kwarg; ``None`` omits it so a
            # base/file-capable adapter without a ``chat_id`` param still accepts
            # the call and telegram falls back to its ``_last_chat_id``.
            if chat_id is not None:
                await cast("_TargetedFileSender", adapter).send_file(
                    file_path, caption_arg, chat_id=chat_id
                )
            else:
                await adapter.send_file(file_path, caption_arg)
        except NotImplementedError as exc:  # B5 — channel cannot carry files
            log.notifications.error(
                "[notifications] deliverer._transport_file: channel does not support files",
                exc_info=exc,
                extra={"_fields": {"channel": channel}},
            )
            return "failed"
        except Exception as exc:  # B5 — upload failed (no retry: upload not idempotent)
            log.notifications.error(
                "[notifications] deliverer._transport_file: send_file failed",
                exc_info=exc,
                extra={"_fields": {"channel": channel}},
            )
            return "failed"

        log.notifications.debug(
            "[notifications] deliverer._transport_file: sent",
            extra={"_fields": {"channel": channel, "has_caption": caption_arg is not None}},
        )
        return "delivered"

    def _log_exit(self, status: DeliveryStatus, channel: str, t0: float) -> None:
        # 4. EXIT
        duration_ms = (_time.monotonic() - t0) * 1000
        log.notifications.debug(
            "[notifications] deliverer.deliver: exit",
            extra={
                "_fields": {
                    "status": status,
                    "channel": channel,
                    "duration_ms": duration_ms,
                }
            },
        )

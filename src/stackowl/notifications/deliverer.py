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
from typing import TYPE_CHECKING, Literal, Protocol, cast, runtime_checkable

from stackowl.infra.observability import log
from stackowl.notifications.router import DeliveryStatus
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.channels.registry import ChannelRegistry
    from stackowl.config.settings import Settings
    from stackowl.notifications.router import Notification, NotificationRouter
    from stackowl.notifications.undelivered_outbox import UndeliveredOutbox


class _ConversationRecorder(Protocol):
    """The one method ESC-19 needs from the conversation store.

    Declared here rather than importing ConversationStore so notifications/ does
    not take a dependency on memory/ — the same reason the undelivered outbox and
    the summary backstop are injected rather than imported.
    """

    async def store(self, content: str, session_key: str) -> None: ...


class _PreferenceReader(Protocol):
    """The one method ESC-20 needs from the preference store.

    Same reasoning as :class:`_ConversationRecorder`: notifications/ states the
    narrow shape it depends on rather than importing PreferenceStore.
    """

    async def list_for_owner(self, owner_key: str) -> dict[str, str]: ...


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


@runtime_checkable
class _EphemeralSender(Protocol):
    """An adapter that can send a silent, self-cleaning probe message.

    Telegram and the socket adapter implement this; slack, discord, whatsapp and
    cli do NOT.

    ``runtime_checkable`` is load-bearing, not decoration. This docstring used to
    claim "at runtime this is only ever invoked for a telegram target" — an
    ASSUMPTION the code did not enforce. The call site ``cast``s to this Protocol
    and calls the method, so any adapter without it raises ``AttributeError`` and
    the notification is LOST. That is not hypothetical: commit 10b4942c records
    "SocketChannelAdapter missing send_ephemeral crashed telegram_canary", and
    the fix then was to add the method to that one adapter rather than to stop
    asserting an unverified capability. Four adapters are still missing it.

    A ``cast`` is a promise to the type checker, never a runtime guarantee.
    """

    async def send_ephemeral(self, chat_id: str | int, text: str) -> int: ...


class _DeletableSender(Protocol):
    """An adapter that can delete a previously-sent message by id.

    Mirrors :class:`_EphemeralSender` — telegram-only cleanup capability used
    to make a health-probe send invisible once delivery is confirmed.
    """

    async def delete_message(self, chat_id: str | int, message_id: int) -> bool: ...


#: Notification categories that are NOT conversation and must never be written
#: into a user's history (ESC-19). ``canary`` is the synthetic send-path health
#: probe — sent, then deleted, so the user never sees it. ``turn_answer`` is the
#: stream-miss fallback for a reply turn_persist has already recorded.
#: The ``ephemeral`` flag is checked separately and covers the general case; these
#: names cover a probe that forgets to set it.
_UNREMEMBERED_CATEGORIES = frozenset({"canary", "turn_answer"})


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
        conversation_store: _ConversationRecorder | None = None,
        preference_store: _PreferenceReader | None = None,
    ) -> None:
        self._router = router
        self._registry = registry
        self._settings = settings
        # ESC-20 — the owner's stored OutputStyle, so a SCHEDULED message obeys
        # the same formatting preferences a conversational reply does. Optional
        # and duck-typed for the same reason as the outbox: an unwired deliverer
        # sends byte-identical text.
        self._preference_store: _PreferenceReader | None = preference_store
        # PA5(b) — the durable NACK store. None keeps every existing test/
        # construction site byte-identical (no silent-drop persistence, same as
        # today); wired for real at assembly time (notifications/assembly.py).
        self._outbox = outbox
        # ESC-19 — where a DELIVERED proactive message is recorded so the agent
        # can talk about what it just said. Optional and duck-typed for the same
        # reason as the outbox: an unwired deliverer behaves exactly as before.
        self._conversation_store: _ConversationRecorder | None = conversation_store

    @property
    def outbox(self) -> UndeliveredOutbox | None:
        """The durable NACK store this deliverer was wired with (PB7b reuse)."""
        return self._outbox

    @property
    def records_conversation(self) -> bool:
        """Whether a delivered message is recorded in the conversation (ESC-19)."""
        return getattr(self, "_conversation_store", None) is not None

    @property
    def applies_output_style(self) -> bool:
        """Whether a proactive message obeys the owner's OutputStyle (ESC-20).

        Read at assembly so the boot log states what was ACTUALLY injected. The
        style transform is a no-op on a message with nothing to transform, so an
        unwired store and a correctly-wired one produce identical delivery logs —
        this is the only thing that distinguishes them.
        """
        return getattr(self, "_preference_store", None) is not None

    async def deliver(
        self, notification: Notification, *, surface_undelivered: bool = True
    ) -> DeliveryStatus:
        """Route + transport ``notification``; never raises.

        Returns the router decision verbatim for ``batched`` / ``suppressed``
        (the router already queued / logged those), the router's ``delivered``
        on a successful ``send_text``, or ``"failed"`` if transport could not
        complete (unknown channel / adapter error after one retry).

        ``surface_undelivered`` (CANARY-LEAK, default ``True`` — preserves exact
        existing behavior for every caller) gates the terminal-failed NACK write
        below. A synthetic probe (PB-CANARY) passes ``False`` so its own marker
        never durably lands in the user-facing undelivered-outbox banner — a
        failed canary send is an operator-alerting signal, not lost user content.
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
                    "surface_undelivered": surface_undelivered,
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

        # ESC-20 — apply the recipient's stored OutputStyle BEFORE transport, so
        # the styled text is what is sent, what an undelivered NACK preserves, and
        # what ESC-19 records in the conversation. Styling after any of those would
        # have the agent remember a message it never sent.
        notification = await self._styled(notification)

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
                channel,
                notification.message,
                chat_id=notification.target_chat_id,
                ephemeral=notification.ephemeral,
            )
        # ADR-2 — a FAILED transport is not surrendered until the RecoveryActuator's
        # reroute rung is exhausted: when an opt-in fallback channel is configured, the
        # message is rerouted there before delivery reports failure (F-64/65/66).
        result = await self._maybe_reroute(channel, notification, result)
        # PA5(b) — a terminal FAILED transport (retry + reroute both exhausted)
        # is a silent drop today: the body is gone, only the status is returned.
        # ADDITIVE: persist the durable NACK; never changes control flow/return.
        if result == "failed" and self._outbox is not None and surface_undelivered:
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
        if result == "delivered":
            await self._remember_what_we_said(notification)
        self._log_exit(result, channel, t0)
        return result

    async def _styled(self, notification: Notification) -> Notification:
        """Return ``notification`` with its body run through the owner's OutputStyle.

        ESC-20, measured 2026-08-16: the style was enforced only in the TURN
        delivery path (``pipeline/steps/deliver.py``). Every proactive and
        scheduled message bypassed it, so Bakir's ``output_tables: off`` — set
        globally, and long predating this — had never once applied to the messages
        he actually finds long: two Sunday Pulses that day were 4,396 and 5,077
        chars against conversational replies of 26 and 1,422.

        DETERMINISTIC HALF ONLY. ``OutputStyle.enforce`` applies markdown, links,
        tables and emoji; its length step is a documented sync no-op, so a
        ``terse`` style does NOT summarise here. That upgrade costs a fast-tier
        call per scheduled send and rewrites a briefing an owl deliberately
        formatted, so it remains ESC-20's open question rather than arriving as a
        side effect of a formatting fix.

        The owner/global precedence is NOT reimplemented — ``load_output_style``
        is the single source, shared with the ``/style`` command. The recipient is
        the owner key, mirroring ``_remember_what_we_said``; with no per-recipient
        preferences the GLOBAL scope still applies, which is the only reason
        Bakir's own style reaches anything at all.

        Never raises and never blocks delivery (B5): an unwired store, an absent
        recipient, an ephemeral probe, or any error returns the notification
        unchanged. A message that arrives unstyled beats a message that does not
        arrive.
        """
        # getattr, not attribute access: several tests build this class via
        # ``__new__`` to exercise ``_transport`` in isolation, so the instance may
        # legitimately have no such attribute. Same guard ``_remember_what_we_said``
        # uses, and it makes an unwired deliverer degrade rather than crash a send.
        store = getattr(self, "_preference_store", None)
        if store is None:
            return notification
        # The health canary is sent and then deleted; restyling a synthetic probe
        # changes what it proves and no human ever reads it. Same exclusion
        # ESC-19 makes for remembering it.
        if bool(getattr(notification, "ephemeral", False)):
            return notification
        body = str(getattr(notification, "message", "") or "")
        if not body.strip():
            return notification
        target = getattr(notification, "target", None) or getattr(
            notification, "target_chat_id", None
        )
        if target is None:
            # No recipient → no per-owner scope to read. The GLOBAL scope still
            # applies, so this is deliberately NOT an early return; the owner key
            # simply resolves to the default principal.
            target = DEFAULT_PRINCIPAL_ID
        try:
            from stackowl.channels._format import load_output_style

            # The channel floor applies HERE too. A proactive message is
            # delivered to a channel exactly like a reply is, so leaving this
            # call channel-blind would have fixed the pipeline seam and left
            # every incident alert and morning brief rendering by preference
            # alone — the same defect, one delivery path over.
            style = await load_output_style(
                store, str(target), channel=notification.channel_name,
            )
            styled = style.enforce(body)
        except Exception as exc:  # B5 — styling must never cost a delivery
            log.notifications.error(
                "[notifications] deliverer: could not apply the output style — "
                "sending the message as-is",
                exc_info=exc,
                extra={"_fields": {"category": notification.category}},
            )
            return notification
        if styled == body:
            return notification
        log.notifications.info(
            "[notifications] deliverer: output style applied to a proactive message",
            extra={"_fields": {"category": notification.category,
                               "channel": notification.channel_name,
                               "before_len": len(body), "after_len": len(styled)}},
        )
        return notification.model_copy(update={"message": styled})

    async def _remember_what_we_said(self, notification: Notification) -> None:
        """Record a DELIVERED proactive message in the recipient's conversation.

        ESC-19, reported by Bakir 2026-08-16: a scheduled headhunter run gathered
        news, delivered it to him on telegram at 14:03:38, and when he replied
        "What?" three times in the next minute he got the answer to a question he
        had asked at 13:19 — because the message existed only under the GOAL lane
        it was generated in (``goal-goal_execution-10da5378``). His own lane had no
        trace of it. THE AGENT HAD NO RECORD OF HAVING SPOKEN TO HIM.

        A message the agent SENT is part of the conversation by any reasonable
        reading, and its absence is what produces "it forgot what it just told me".

        FORMAT. Stored as ``"User:\\n\\nAssistant: <text>"`` — an EMPTY user half.
        classify's ``_parse_turns_to_messages`` partitions on ``"\\n\\nAssistant:"``
        and skips a blank half, so this reads back as a lone assistant turn, which
        is exactly what an unprompted message is. Inventing a fake user turn would
        put words in his mouth.

        KEY. ``notification.target`` — the same identity the undelivered outbox
        attributes a failed send to, so a delivered and an undelivered message are
        filed under one key. The conversation reader unions the identity and lane
        keys (fixed earlier today), so this is visible either way.

        Never raises and never changes delivery: the message HAS been sent by the
        time this runs. Failing to remember it must not turn a delivered message
        into a failed one.
        """
        # getattr on SELF too. Several tests build this class with
        # ``ProactiveDeliverer.__new__`` and set only the attributes they need, so
        # __init__ never runs and the attribute does not exist. More importantly
        # this method runs AFTER a successful send: an instance shaped differently
        # than expected must not turn a delivered message into an AttributeError.
        store = getattr(self, "_conversation_store", None)
        if store is None:
            return
        # getattr, not attribute access. This runs AFTER a successful send, so an
        # AttributeError here would turn a delivered message into a crash — and
        # the reads were outside the try below, which is exactly how it broke
        # test_deliver_threads_notification_target_chat_id on a notification
        # shaped without `target`. Remembering must never cost the delivery.
        # A TURN ANSWER IS ALREADY REMEMBERED. pipeline/steps/deliver.py routes the
        # stream-miss fallback through this same chokepoint with
        # category="turn_answer", and that reply is persisted by turn_persist on
        # the normal path. Recording it here too would put the answer in the
        # user's history TWICE — and the duplicate would arrive with an empty user
        # half, so the conversation would read as if the agent had said it
        # unprompted. This hook is for messages the user never asked for.
        if str(getattr(notification, "category", "") or "") == "turn_answer":
            return
        # AND A MESSAGE THE USER NEVER SEES IS NOT CONVERSATION. The telegram
        # canary is a synthetic 17-character health probe that is SENT and then
        # DELETED to verify the send path; it is delivered through this same
        # chokepoint. Caught on live traffic 20 minutes after ESC-19 shipped —
        # two canaries had already been written into Bakir's history with
        # category="canary". Anything ephemeral is a probe, not something the
        # agent said, and remembering it would have the agent "recall" messages
        # the user was never shown.
        if bool(getattr(notification, "ephemeral", False)):
            return
        if str(getattr(notification, "category", "") or "") in _UNREMEMBERED_CATEGORIES:
            return
        target = getattr(notification, "target", None) or getattr(
            notification, "target_chat_id", None
        )
        if not target:
            # No recipient to attribute it to — a broadcast or a single-terminal
            # channel using the adapter's shared chat. Recording it under a guess
            # would put the message in somebody else's history.
            log.notifications.info(
                "[notifications] deliverer: delivered with no attributable recipient "
                "— not recorded in a conversation",
                extra={"_fields": {
                    "channel": getattr(notification, "channel_name", None),
                    "category": getattr(notification, "category", None),
                }},
            )
            return
        body = str(getattr(notification, "message", "") or "").strip()
        if not body:
            return
        try:
            await store.store(f"User:\n\nAssistant: {body}", str(target))
        except Exception as exc:
            log.notifications.error(
                "[notifications] deliverer: could not record the delivered message — "
                "the agent will not remember sending it",
                exc_info=exc,
                extra={"_fields": {"target": str(target),
                                   "category": getattr(notification, "category", None)}},
            )
            return
        log.notifications.info(
            "[notifications] deliverer: recorded the delivered message in the "
            "recipient's conversation",
            extra={"_fields": {"target": str(target), "chars": len(body),
                               "category": getattr(notification, "category", None)}},
        )

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
        self,
        channel: str,
        message: str,
        *,
        chat_id: str | int | None = None,
        ephemeral: bool = False,
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

        ``ephemeral`` (default ``False``, byte-identical for every existing
        caller) is the health-canary path: when set AND a concrete ``chat_id``
        is available, the message is sent via ``send_ephemeral`` (silent,
        muted) instead of ``send_text``, then best-effort deleted once sent —
        so the probe proves the real send path without leaving a visible
        message behind. A delete failure never flips an honest "delivered"
        into "failed" (cosmetic cleanup only). With ``ephemeral=False`` or no
        resolved ``chat_id`` (non-telegram / unresolved), this falls through to
        the normal send path unchanged.

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
                if ephemeral and chat_id is not None and isinstance(adapter, _EphemeralSender):
                    message_id = await cast("_EphemeralSender", adapter).send_ephemeral(
                        chat_id, message
                    )
                    await self._best_effort_delete(adapter, chat_id, message_id)
                elif ephemeral and chat_id is not None:
                    # The adapter cannot self-clean. DEGRADE to an ordinary send
                    # rather than raising: an alert that arrives and stays is
                    # vastly better than an alert that is lost because it could
                    # not be tidied up afterwards.
                    log.notifications.warning(
                        "[notifications] deliverer._transport: adapter cannot send "
                        "ephemeral — delivering as a normal message instead",
                        extra={"_fields": {
                            "channel": channel,
                            "adapter": type(adapter).__name__,
                        }},
                    )
                    await cast("_TargetedSender", adapter).send_text(
                        message, chat_id=chat_id
                    )
                elif chat_id is not None:
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
                            "ephemeral": ephemeral,
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

    async def _best_effort_delete(
        self, adapter: object, chat_id: str | int, message_id: int
    ) -> None:
        """Clean up a sent ephemeral probe; a delete failure is cosmetic only.

        Swallows its OWN exception — a cleanup miss must never flip an honest
        "delivered" transport result into "failed".
        """
        try:
            await cast("_DeletableSender", adapter).delete_message(chat_id, message_id)
        except Exception as exc:  # B5 — cleanup failure is cosmetic, never fails delivery
            log.notifications.error(
                "[notifications] deliverer._best_effort_delete: delete failed — cosmetic",
                exc_info=exc,
                extra={"_fields": {"chat_id": chat_id, "message_id": message_id}},
            )

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

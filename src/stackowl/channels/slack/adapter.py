"""SlackChannelAdapter — Slack Bolt-backed channel adapter for StackOwl.

The adapter does NOT open the live Socket Mode / HTTP connection itself; the
production runner (or an integration test harness) is responsible for wiring
the underlying :class:`slack_bolt.async_app.AsyncApp` to ``handle_event`` and
``handle_slash_command``. This keeps the adapter unit-testable without
network I/O while preserving a clean integration seam.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from stackowl.channels.base import ChannelAdapter
from stackowl.channels.splitter import SlackMessageSplitter
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import DeliveryError
from stackowl.gateway.scanner import IngressMessage
from stackowl.health.status import HealthStatus
from stackowl.infra.observability import log
from stackowl.pipeline.streaming import ResponseChunk

from .helpers import (
    ActionsBlock,
    ButtonElement,
    PlainText,
    SectionBlock,
    hash_user_id,
    is_authorized,
    strip_bot_mention,
    to_slack_mrkdwn,
)
from .settings import SlackSettings

if TYPE_CHECKING:
    from stackowl.channels.registry import ChannelRegistry


_HEALTH_STALE_AFTER_S = 90.0

# Sentinel distinguishing "no target kwarg passed" (proactive/best-effort →
# logged no-op on miss) from "target explicitly passed" (on-turn → raise on an
# unresolvable miss). ``None`` alone is ambiguous: ``send()`` may pass
# ``target=None`` after narrowing a stray non-str target on the on-turn path,
# which MUST fail loud rather than silently drop a turn's answer (C6 / C-1).
_UNSET: Any = object()


def _is_ratelimited(err: BaseException) -> bool:
    """True if ``err`` looks like a transient Slack rate-limit.

    Slack's SDK raises ``SlackApiError`` carrying a ``response["error"]`` of
    ``"ratelimited"`` (HTTP 429). We avoid importing the SDK type here (unit
    tests don't have the package) and instead probe defensively. Slack-specific
    error names live ONLY in this thin adapter.
    """
    resp = getattr(err, "response", None)
    code = None
    if isinstance(resp, dict):
        code = resp.get("error")
    else:
        code = getattr(resp, "get", lambda _k: None)("error") if resp is not None else None
    if code == "ratelimited":
        return True
    return "ratelimited" in str(err).lower()


class SlackChannelAdapter(ChannelAdapter):
    """Slack channel adapter — see module docstring for the integration contract."""

    contributor_name: str = "slack_channel"

    # Bound on the per-turn state maps (thread-by-trace, inbound-files-by-trace).
    # A turn's entry is read once at send/fetch time; the FIFO cap evicts the
    # oldest turns so a long-lived process never leaks. Generous so an in-flight
    # turn's entry is never evicted under realistic concurrency.
    _TURN_STATE_MAX: int = 1024

    @staticmethod
    def _put_bounded(store: dict[str, list[str]] | dict[str, str], key: str, value: object) -> None:
        """Insert keyed turn state, evicting the OLDEST entry past the bound (FIFO).

        A plain dict preserves insertion order, so the first key is the oldest.
        Re-inserting an existing key refreshes its value in place (order kept).
        """
        store[key] = value  # type: ignore[assignment]
        while len(store) > SlackChannelAdapter._TURN_STATE_MAX:
            oldest = next(iter(store))
            store.pop(oldest, None)

    def __init__(self, settings: SlackSettings) -> None:
        log.slack.debug(
            "[slack] adapter.init: entry",
            extra={
                "_fields": {
                    "socket_mode": settings.socket_mode,
                    "allowed_count": len(settings.allowed_user_ids),
                }
            },
        )
        self._settings = settings
        self._queue: asyncio.Queue[IngressMessage] = asyncio.Queue()
        self._splitter = SlackMessageSplitter()
        self._bot_user_id: str = ""
        self._session_counter = 0
        self._last_ping_at: datetime | None = None
        # Session→target map (load-bearing): session_id → the Slack channel id
        # that this session's replies route to. Phase B's consent + clarify
        # resolve the Slack destination from THIS map because the session_id
        # (``slack:{hash}``) is NOT itself a send target. ``_last_target`` is the
        # single-terminal fallback when a chunk carries no explicit target.
        self._targets: dict[str, str] = {}
        self._last_target: str | None = None
        # Per-CHANNEL thread map: channel_id → thread_ts, for OUT-OF-BAND callers
        # (send_file mid-turn, consent/clarify prompts) that resolve by channel id
        # rather than by turn. Best-effort latest-wins for those paths. The on-turn
        # send() path uses the authoritative per-trace map below instead. The
        # global ``_last_thread`` fallback was REMOVED (F011 mis-thread hazard).
        self._threads: dict[str, str] = {}
        # Per-TURN thread map (F011): trace_id → the originating thread_ts. A turn
        # owns its trace_id, so resolving the reply thread from THIS map (not the
        # per-channel ``_threads`` or the global ``_last_thread``) means a newer
        # concurrent event for the same channel/user can never mis-thread an
        # earlier turn's reply. Bounded FIFO (``_TURN_STATE_MAX``) so it never
        # grows unbounded across a long-lived process.
        self._thread_by_trace: dict[str, str] = {}
        # Inbound-files map (F010): trace_id → the Slack file id(s) attached to the
        # inbound event that minted this turn. Previously keyed by session_id
        # (``slack:{hash(user)}`` — shared across ALL of a user's messages), so a
        # later FILELESS same-user event cleared an earlier turn's ids before it
        # fetched them. Keying by the turn-owned trace_id makes the ids immune to
        # any later event. ``IngressMessage`` carries no media field (it would
        # ripple through the frozen dataclass + whole pipeline), so ids are
        # surfaced here for a turn to fetch via ``download_media``. Bounded FIFO.
        self._inbound_files: dict[str, list[str]] = {}
        # The live AsyncApp is injected by the integration runner — we keep an
        # untyped reference so the adapter remains importable without
        # slack_bolt being on the path at import time.
        self._app: object | None = None
        # ADR-6 — the real reconnect callback, injected by the production
        # runner via `set_reconnector()` (mirrors `set_bolt_app`). The runner
        # owns the live `AsyncSocketModeHandler`; the adapter never imports
        # slack_bolt itself. None until injected (e.g. an adapter constructed
        # outside the full boot path, such as in isolated unit tests).
        self._reconnector: Callable[[], Awaitable[None]] | None = None
        log.slack.info(
            "[slack] adapter.init: exit",
            extra={"_fields": {"channel": self.channel_name}},
        )

    # ------------------------------------------------------------------ #
    # ChannelAdapter contract
    # ------------------------------------------------------------------ #

    @property
    def channel_name(self) -> str:
        return "slack"

    def resolve_target(self, session_id: str) -> str | int | None:
        """Resolve the Slack channel id for ``session_id`` (C1/F104).

        The ``slack:{hash}`` session_id is NOT itself a send target, so this
        delegates to the adapter-owned :meth:`target_for_session` / ``_targets``
        map (the asymmetry is honored by keeping resolution in the adapter that
        owns the map). Returns the Slack-native ``str`` channel id, or ``None``
        for an unknown session (the caller then records undeliverable, never a
        guess).
        """
        target = self.target_for_session(session_id)
        log.slack.debug(
            "[slack] adapter.resolve_target: resolved",
            extra={"_fields": {"resolved": target is not None}},
        )
        return target

    def target_for_session(self, session_id: str) -> str | None:
        """Resolve the Slack send destination (channel id) for a session.

        Phase B (consent / clarify) calls this to find where to deliver an
        out-of-band prompt, since the ``slack:{hash}`` session_id is not itself
        a send target. Returns ``None`` for an unknown session.
        """
        return self._targets.get(session_id)

    def inbound_files_for_trace(self, trace_id: str) -> list[str]:
        """Return the Slack file id(s) attached to the event that minted this turn.

        Keyed by the turn-owned ``trace_id`` (F010) so a later same-user event can
        never clear an earlier turn's ids. The turn fetches each file's bytes via
        :meth:`download_media`. Returns an empty list for an unknown trace or an
        event with no files — never fabricates ids.
        """
        return list(self._inbound_files.get(trace_id, []))

    async def start(self) -> None:
        """Prepare the adapter for live use (no network I/O happens here)."""
        TestModeGuard.assert_not_test_mode("slack.start")
        log.slack.debug(
            "[slack] adapter.start: entry",
            extra={"_fields": {"socket_mode": self._settings.socket_mode}},
        )
        mode = "socket_mode" if self._settings.socket_mode else "http_webhook"
        log.slack.info(
            "[slack] adapter.start: would connect",
            extra={"_fields": {"mode": mode}},
        )
        # The production runner attaches `_app` after import (via
        # `set_bolt_app`) and calls AsyncApp.start_async() itself.
        log.slack.debug("[slack] adapter.start: exit")

    async def receive(self) -> IngressMessage:
        msg = await self._queue.get()
        log.slack.debug(
            "[slack] adapter.receive: yielded message",
            extra={
                "_fields": {
                    "session_id": msg.session_id,
                    "trace_id": msg.trace_id,
                    "text_len": len(msg.text),
                }
            },
        )
        return msg

    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None:
        TestModeGuard.assert_not_test_mode("slack.send")
        log.slack.debug("[slack] adapter.send: entry")
        buffer = ""
        # Per-turn delivery target: a turn's chunks all carry the SAME `target`
        # (the originating channel id stamped at deliver-time). Capture it so this
        # turn replies to ITS OWN channel, not the shared `_last_target` (which a
        # newer concurrent inbound event may have overwritten). None →
        # send_text falls back to `_last_target` (single-terminal/back-compat).
        target: str | None = None
        # Per-turn thread (F011): capture this turn's trace_id so the reply threads
        # under ITS originating thread, not a stale per-channel / global thread a
        # newer concurrent event may have overwritten.
        turn_trace: str | None = None
        async for chunk in chunks:
            buffer += chunk.content
            if chunk.trace_id:
                turn_trace = chunk.trace_id
            raw = chunk.target
            if isinstance(raw, str):
                target = raw
            elif isinstance(raw, int):
                # Slack only ever delivers str channel-id targets; an int
                # (Telegram chat_id) cannot reach the Slack adapter by
                # construction (each turn is delivered by its OWN channel
                # adapter). Log loudly, then fall back to `_last_target`.
                log.slack.warning(
                    "[slack] adapter.send: unexpected int target — falling back to _last_target",
                    extra={"_fields": {"target": raw}},
                )
                target = None
        # Per-turn thread is AUTHORITATIVE (F011). When the turn's trace has no
        # stamped thread (a best-effort / legacy caller whose chunk carries no real
        # inbound trace), fall back to the per-channel map — NOT the removed global
        # _last_thread, which could adopt a different turn's thread.
        thread_ts = self._thread_by_trace.get(turn_trace) if turn_trace else None
        if thread_ts is None:
            dest_for_thread = target if target is not None else self._last_target
            if dest_for_thread is not None:
                thread_ts = self._threads.get(dest_for_thread)
        log.slack.debug(
            "[slack] adapter.send: decision collected",
            extra={
                "_fields": {
                    "total_len": len(buffer),
                    "explicit_target": target is not None,
                    "threaded": thread_ts is not None,
                }
            },
        )
        await self.send_text(buffer, target=target, thread_ts=thread_ts)
        log.slack.debug(
            "[slack] adapter.send: exit",
            extra={"_fields": {"explicit_target": target is not None}},
        )

    async def send_text(
        self, text: str, *, target: str | None = _UNSET, thread_ts: str | None = None
    ) -> None:
        """Split ``text`` per Slack's limit and post each part to ``target``.

        ``target`` is a Slack channel id (the per-message destination threaded
        from ``IngressMessage.chat_id`` → ``ResponseChunk.target``); when omitted
        it falls back to ``self._last_target`` for back-compat callers (proactive
        deliverer, clarify degrade-path).

        ``thread_ts`` (F011) is the per-TURN originating thread resolved by
        :meth:`send` from the turn's ``trace_id``. It is passed EXPLICITLY rather
        than re-derived adapter-side from the channel id, because a per-channel /
        global thread map can be overwritten by a newer concurrent event for the
        same channel and would mis-thread this turn's reply. ``None`` → reply to
        the channel root (a DM, or a best-effort caller with no turn context).

        No-target contract (C6 / C-1): an EXPLICIT ``target`` (the on-turn
        ``send()`` path) that fails to resolve → log ``error`` + raise
        ``DeliveryError("slack", "no_target")`` (a turn's answer is never silently
        dropped). ``target`` OMITTED (proactive/best-effort) with no
        ``_last_target`` → loud ``error``-level logged NO-OP, never a raise
        (preserves the proactive deliverer never-raises contract).
        """
        TestModeGuard.assert_not_test_mode("slack.send_text")
        explicit = target is not _UNSET
        resolved = target if explicit else None
        dest = resolved if resolved is not None else self._last_target
        log.slack.debug(
            "[slack] adapter.send_text: entry",
            extra={
                "_fields": {
                    "text_len": len(text),
                    "explicit_target": explicit,
                    "threaded": thread_ts is not None,
                }
            },
        )
        if dest is None:
            if explicit:
                log.slack.error(
                    "[slack] adapter.send_text: explicit target unresolvable — failing loud",
                    extra={"_fields": {"text_len": len(text)}},
                )
                raise DeliveryError("slack", "no_target")
            log.slack.error(
                "[slack] adapter.send_text: no target channel (best-effort) — message dropped",
                extra={"_fields": {"text_len": len(text)}},
            )
            return
        # Convert assistant GFM → Slack mrkdwn BEFORE splitting so the splitter
        # counts final mrkdwn chars (e.g. ``**bold**``→``*bold*`` shrinks the
        # text). Code spans/fences are preserved verbatim by the converter.
        # Split-safety (CHAN-3 / F007): the splitter cuts on paragraph/sentence/
        # grapheme boundaries, retreats out of an open code fence, AND retreats a
        # cut that lands inside a ``<url|text>`` link span so a link is never
        # severed mid-chunk (a link longer than the whole limit still hard-splits
        # to make progress).
        mrkdwn = to_slack_mrkdwn(text)
        log.slack.debug(
            "[slack] adapter.send_text: decision converted to mrkdwn",
            extra={"_fields": {"in_len": len(text), "out_len": len(mrkdwn)}},
        )
        parts = self._splitter.split(mrkdwn)
        for idx, part in enumerate(parts):
            await self._post_text(part, index=idx, channel=dest, thread_ts=thread_ts)
        log.slack.debug(
            "[slack] adapter.send_text: exit",
            extra={"_fields": {"part_count": len(parts)}},
        )

    async def send_file(
        self, file_path: str, caption: str | None = None, *, target: str | None = None
    ) -> None:
        """Upload ``file_path`` to a Slack channel via ``files_upload_v2``.

        The destination is resolved EXACTLY as :meth:`send_text` does: an explicit
        ``target`` (the per-message channel id) wins, otherwise ``self._last_target``
        (single-terminal/back-compat). The per-channel ``thread_ts`` (if any) threads
        the upload under the originating message — a channel reply threads, a DM
        uploads to the channel root. ``caption`` becomes Slack's ``initial_comment``.

        Self-healing: a missing client or an unresolved destination is a logged
        no-op (the :class:`ProactiveDeliverer` maps undeliverable to ``failed``);
        any upload error is logged and swallowed so a file send never crashes the
        turn. ``TestModeGuard`` blocks live I/O in tests.
        """
        dest = target if target is not None else self._last_target
        # Per-channel thread (best-effort for out-of-band file sends). The global
        # _last_thread fallback is removed — it could mis-thread under concurrency
        # (F011). A DM or unknown channel → channel root.
        thread_ts = self._threads.get(dest) if dest is not None else None
        log.slack.debug(
            "[slack] adapter.send_file: entry",
            extra={
                "_fields": {
                    "explicit_target": target is not None,
                    "has_caption": bool(caption),
                    "threaded": thread_ts is not None,
                }
            },
        )
        TestModeGuard.assert_not_test_mode("slack.send_file")
        client = self._client()
        if client is None or dest is None:
            log.slack.warning(
                "[slack] adapter.send_file: no client/target — file dropped",
                extra={
                    "_fields": {"has_client": client is not None, "has_dest": dest is not None}
                },
            )
            return
        upload_kwargs: dict[str, object] = {
            "channel": dest,
            "file": file_path,
            "initial_comment": caption or None,
        }
        if thread_ts is not None:
            upload_kwargs["thread_ts"] = thread_ts
        log.slack.debug(
            "[slack] adapter.send_file: step upload",
            extra={"_fields": {"channel": dest, "threaded": thread_ts is not None}},
        )
        try:
            await client.files_upload_v2(**upload_kwargs)
            log.slack.debug(
                "[slack] adapter.send_file: exit uploaded",
                extra={"_fields": {"channel": dest}},
            )
        except Exception as exc:  # self-healing — a file send must not crash the turn
            log.slack.error(
                "[slack] adapter.send_file: upload failed",
                exc_info=exc,
                extra={"_fields": {"channel": dest}},
            )

    async def download_media(self, file_id: str) -> bytes:
        """Download an inbound Slack file's bytes by its file id.

        Slack inbound files carry a ``url_private`` reachable only with the bot
        token. The Bolt client doesn't expose a raw byte-fetch, so we resolve the
        ``url_private`` via ``files_info(file=file_id)`` then perform an authorized
        ``GET`` (``Authorization: Bearer <bot_token>``) with ``httpx`` (already a
        project dependency). Per the no-hidden-errors rule a genuine download
        failure (no client, missing url, network/HTTP error) is logged loudly and
        re-raised — never a silent empty ``b""``.
        """
        log.slack.debug(
            "[slack] adapter.download_media: entry",
            extra={"_fields": {"file_id_len": len(file_id)}},
        )
        TestModeGuard.assert_not_test_mode("slack.download_media")
        client = self._client()
        if client is None:
            log.slack.error(
                "[slack] adapter.download_media: no Bolt client — cannot download",
                extra={"_fields": {"file_id_len": len(file_id)}},
            )
            raise RuntimeError("slack download_media: no Bolt client attached")
        try:
            log.slack.debug("[slack] adapter.download_media: step files_info")
            info = await client.files_info(file=file_id)
            file_obj = info.get("file") if isinstance(info, dict) else None
            url_private = (
                file_obj.get("url_private") if isinstance(file_obj, dict) else None
            )
            if not isinstance(url_private, str) or not url_private:
                log.slack.error(
                    "[slack] adapter.download_media: files_info returned no url_private",
                    extra={"_fields": {"file_id_len": len(file_id)}},
                )
                raise RuntimeError("slack download_media: no url_private for file")
            log.slack.debug(
                "[slack] adapter.download_media: step authorized GET",
                extra={"_fields": {"has_url": True}},
            )
            import httpx

            headers = {"Authorization": f"Bearer {self._settings.bot_token}"}
            async with httpx.AsyncClient() as http:
                resp = await http.get(url_private, headers=headers)
                resp.raise_for_status()
                data = bytes(resp.content)
        except Exception as exc:
            log.slack.error(
                "[slack] adapter.download_media: fetch failed",
                exc_info=exc,
                extra={"_fields": {"file_id_len": len(file_id)}},
            )
            raise
        log.slack.debug(
            "[slack] adapter.download_media: exit",
            extra={"_fields": {"bytes_len": len(data)}},
        )
        return data

    async def send_clarify(
        self,
        session_id: str,
        question: str,
        choices: tuple[str, ...] | list[str],
        clarify_id: str,
    ) -> None:
        """Deliver a clarify question as tap-buttons (one Block Kit button/choice).

        The Slack destination is resolved from ``target_for_session(session_id)``
        — the ``slack:{hash}`` session_id is NOT itself a send target. When that
        returns ``None`` we degrade to the numbered-text fallback (best-effort to
        ``_last_target``) rather than guessing a channel.

        Each non-blank choice becomes a button whose ``action_id``/``value`` is
        ``clarify:{clarify_id}:{idx}`` — a tap is routed to
        :class:`~stackowl.channels.slack.clarify.SlackClarifyResolver`, which maps
        ``idx`` back to the choice text and wakes the parked turn. The ORIGINAL
        index is PRESERVED even when earlier choices are blank (mirrors Telegram),
        so ``clarify:{id}:{idx}`` always indexes the gateway's stored
        ``entry.choices[idx]``. Open-ended questions (no non-blank choices) post
        as plain text and are answered by typing.

        Self-healing: an unresolved target, a missing client, or any delivery
        error degrades to a best-effort numbered-text post — a delivery failure
        must never crash the turn (the gateway treats ``send_clarify`` as
        best-effort).
        """
        n_nonblank = sum(1 for c in choices if str(c).strip())
        log.slack.debug(
            "[slack] adapter.send_clarify: entry",
            extra={"_fields": {"n_choices": n_nonblank, "clarify_id": clarify_id}},
        )
        # 2. DECISION — resolve the Slack destination from the session→channel map.
        dest = self.target_for_session(session_id)
        if dest is None:
            log.slack.warning(
                "[slack] adapter.send_clarify: no target for session — text fallback",
                extra={"_fields": {"session_id": session_id, "clarify_id": clarify_id}},
            )
            await self._send_clarify_text_fallback(question, choices, channel=None)
            return

        if not n_nonblank:
            # Open-ended question — answered by typing (no buttons).
            log.slack.debug(
                "[slack] adapter.send_clarify: decision no_choices — plain text",
                extra={"_fields": {"channel": dest}},
            )
            await self._send_clarify_text_fallback(question, choices, channel=dest)
            return

        client = self._client()
        if client is None:
            log.slack.warning(
                "[slack] adapter.send_clarify: no Bolt client — text fallback",
                extra={"_fields": {"clarify_id": clarify_id}},
            )
            await self._send_clarify_text_fallback(question, choices, channel=dest)
            return

        try:
            buttons: list[ButtonElement] = []
            for idx, choice in enumerate(choices):
                label = str(choice).strip()
                if not label:
                    continue
                action = f"clarify:{clarify_id}:{idx}"
                buttons.append(
                    ButtonElement(
                        text=PlainText(text=label),
                        action_id=action,
                        value=action,
                    )
                )
            blocks = [
                SectionBlock(text=PlainText(text=question)).model_dump(),
                ActionsBlock(elements=buttons).model_dump(),
            ]
            log.slack.debug(
                "[slack] adapter.send_clarify: step blocks_built",
                extra={"_fields": {"channel": dest, "n_buttons": len(buttons)}},
            )
            post_kwargs: dict[str, object] = {
                "channel": dest,
                "text": question,
                "blocks": blocks,
            }
            thread_ts = self._threads.get(dest)
            if thread_ts is not None:
                post_kwargs["thread_ts"] = thread_ts
            await client.chat_postMessage(**post_kwargs)
        except Exception as exc:  # self-healing — any failure → best-effort text
            log.slack.error(
                "[slack] adapter.send_clarify: button delivery failed — text fallback",
                exc_info=exc,
                extra={"_fields": {"channel": dest, "clarify_id": clarify_id}},
            )
            await self._send_clarify_text_fallback(question, choices, channel=dest)
            return
        log.slack.debug(
            "[slack] adapter.send_clarify: exit",
            extra={"_fields": {"channel": dest, "delivered": True}},
        )

    async def _send_clarify_text_fallback(
        self,
        question: str,
        choices: tuple[str, ...] | list[str],
        *,
        channel: str | None,
    ) -> None:
        """Best-effort numbered-text delivery of a clarify question (never raises).

        Renders ``question`` followed by a numbered list of the non-blank choices
        (a glyph-free, language-neutral ``N. choice`` cadence). Posts directly via
        the Bolt client so it works regardless of TestModeGuard; ``channel=None``
        falls back to ``_last_target`` (single-terminal/back-compat path).
        """
        dest = channel if channel is not None else self._last_target
        lines = [question]
        n = 0
        for choice in choices:
            label = str(choice).strip()
            if not label:
                continue
            n += 1
            lines.append(f"{n}. {label}")
        body = "\n".join(lines)
        try:
            client = self._client()
            if client is None or dest is None:
                log.slack.warning(
                    "[slack] adapter.send_clarify: text fallback has no target/client — dropped",
                    extra={"_fields": {"has_client": client is not None, "has_dest": dest is not None}},
                )
                return
            post_kwargs: dict[str, object] = {"channel": dest, "text": body}
            thread_ts = self._threads.get(dest)
            if thread_ts is not None:
                post_kwargs["thread_ts"] = thread_ts
            await client.chat_postMessage(**post_kwargs)
            log.slack.debug(
                "[slack] adapter.send_clarify: text fallback posted",
                extra={"_fields": {"channel": dest, "n_choices": n}},
            )
        except Exception as exc:  # truly best-effort — never raise into the turn
            log.slack.error(
                "[slack] adapter.send_clarify: text fallback post failed",
                exc_info=exc,
                extra={"_fields": {"channel": dest}},
            )

    async def acknowledge_callback(self, callback_id: str, text: str = "") -> None:
        """No-op for Slack — block_actions are ack'd per-handler by Bolt's ``ack()``.

        Slack requires the ``block_actions`` HTTP handler to call ``ack()`` within
        3 seconds; that acknowledgement is owned by the Bolt ``@app.action``
        handler (B3), NOT by an out-of-band call here. This method exists to honor
        the channel-adapter contract shared with Telegram (whose
        ``acknowledge_callback`` answers a callback_query) so cross-channel callers
        (e.g. the memory handlers) stay uniform; on Slack it is a documented no-op.
        """
        log.slack.debug(
            "[slack] adapter.acknowledge_callback: no-op (ack owned by Bolt handler)",
            extra={"_fields": {"callback_id_len": len(callback_id), "has_text": bool(text)}},
        )

    async def _chat_update(
        self,
        channel: str,
        ts: str,
        text: str,
        blocks: list[dict[str, object]] | None = None,
    ) -> bool:
        """Best-effort rewrite of an existing message (drops buttons by default).

        Used to turn an interactive prompt into a resolved decision after a tap so
        it can't be re-tapped. Fail-open: any Bolt failure is LOGGED and returns
        ``False`` rather than raising — a failed cosmetic update must never break a
        decision that is already recorded.
        """
        log.slack.debug(
            "[slack] adapter._chat_update: entry",
            extra={"_fields": {"channel": channel, "text_len": len(text)}},
        )
        client = self._client()
        if client is None:
            log.slack.warning("[slack] adapter._chat_update: no client — skipped")
            return False
        update_kwargs: dict[str, object] = {"channel": channel, "ts": ts, "text": text}
        if blocks is not None:
            update_kwargs["blocks"] = blocks
        try:
            await client.chat_update(**update_kwargs)
            log.slack.debug("[slack] adapter._chat_update: exit updated")
            return True
        except Exception as exc:  # fail-open — decision already recorded
            log.slack.error(
                "[slack] adapter._chat_update: update failed",
                exc_info=exc,
                extra={"_fields": {"channel": channel}},
            )
            return False

    def _client(self) -> Any | None:
        """Return the live Bolt client (``app.client``), or None when unattached.

        Typed ``Any`` so the dynamically-shaped Bolt client (``chat_postMessage`` /
        ``chat_update``) stays callable without importing slack_sdk at module load
        — mirrors the consent prompter's ``_client`` seam.
        """
        app = self._app
        if app is None:
            return None
        return getattr(app, "client", None)

    # ------------------------------------------------------------------ #
    # Integration hooks called by the live AsyncApp
    # ------------------------------------------------------------------ #

    def set_bot_user_id(self, bot_user_id: str) -> None:
        """Record the bot's own user ID so mentions can be stripped."""
        log.slack.debug(
            "[slack] adapter.set_bot_user_id: entry",
            extra={"_fields": {"bot_id_present": bool(bot_user_id)}},
        )
        self._bot_user_id = bot_user_id

    def set_bolt_app(self, app: object) -> None:
        """Attach a live ``slack_bolt.async_app.AsyncApp`` instance."""
        log.slack.debug(
            "[slack] adapter.set_bolt_app: entry",
            extra={"_fields": {"has_app": app is not None}},
        )
        self._app = app

    def set_reconnector(self, reconnector: Callable[[], Awaitable[None]]) -> None:
        """Attach the real reconnect callback (ADR-6, injected by the production runner).

        Mirrors :meth:`set_bolt_app`: the adapter never imports ``slack_bolt``
        itself. The runner (``orchestrator.py``) owns the live
        ``AsyncSocketModeHandler``/task pair and hands the adapter a zero-arg
        async callable that tears down the current socket-mode connection and
        rebuilds a fresh one (reusing the existing ``AsyncApp`` and all its
        registered event/action/command handlers). :meth:`ensure_available`
        invokes this callback to perform an actual reconnect.
        """
        log.slack.debug(
            "[slack] adapter.set_reconnector: entry",
            extra={"_fields": {"has_reconnector": reconnector is not None}},
        )
        self._reconnector = reconnector

    def mark_ping(self) -> None:
        """Called by the connection heartbeat — keeps health_check honest."""
        self._last_ping_at = datetime.now(tz=UTC)

    async def handle_event(
        self, event: dict[str, object], user_id: str, text: str
    ) -> None:
        """Filter and enqueue an inbound Slack event.

        Unauthorized senders are dropped silently (fail-closed) with a warning
        log that records only the sha256 hash of the user ID. Bot self-mentions
        are stripped before the message reaches the gateway router.
        """
        log.slack.debug(
            "[slack] adapter.handle_event: entry",
            extra={
                "_fields": {
                    "user_hash": hash_user_id(user_id),
                    "text_len": len(text),
                    "event_type": str(event.get("type", "")),
                }
            },
        )
        if not is_authorized(user_id, self._settings.allowed_user_ids):
            log.slack.warning(
                "[slack] adapter.handle_event: dropping unauthorized sender",
                extra={"_fields": {"user_hash": hash_user_id(user_id)}},
            )
            return

        cleaned = strip_bot_mention(text, self._bot_user_id) if self._bot_user_id else text.strip()
        log.slack.debug(
            "[slack] adapter.handle_event: decision cleaned",
            extra={"_fields": {"cleaned_len": len(cleaned)}},
        )

        self._session_counter += 1
        session_id = f"slack:{hash_user_id(user_id)}"
        trace_id = f"slack-{hash_user_id(user_id)}-{self._session_counter}"

        # Resolve the Slack send destination. The event carries `channel` (the
        # routing key) and optionally `thread_ts`/`ts`. The chunk.target is kept
        # a SIMPLE channel-id string; the thread_ts is resolved adapter-side at
        # send time. So a channel message threads its reply under the originating
        # thread, while a DM (no thread_ts) replies to the channel directly.
        channel_id = str(event.get("channel", ""))
        raw_thread = event.get("thread_ts") or event.get("ts")
        thread_ts = str(raw_thread) if raw_thread is not None else None

        # C2 — surface inbound files so the turn can fetch them via
        # ``download_media``. Slack puts uploads in an event-level ``files``
        # array, each carrying an ``id``. We extract the ids only (never log raw
        # file contents) and key them by session_id so the turn already owns the
        # lookup. Non-list/missing → no files (no fabricated ids).
        raw_files = event.get("files")
        file_ids: list[str] = []
        if isinstance(raw_files, list):
            for f in raw_files:
                if isinstance(f, dict):
                    fid = f.get("id")
                    if isinstance(fid, str) and fid:
                        file_ids.append(fid)
        log.slack.debug(
            "[slack] adapter.handle_event: decision target_resolved",
            extra={
                "_fields": {
                    "channel": channel_id,
                    "threaded": thread_ts is not None,
                    "file_count": len(file_ids),
                }
            },
        )

        msg = IngressMessage(
            text=cleaned,
            session_id=session_id,
            channel=self.channel_name,
            trace_id=trace_id,
            # Stamp the routing channel id so this turn delivers back to ITS OWN
            # channel — never the shared `_last_target`, which a newer concurrent
            # inbound event may overwrite before this turn finishes.
            chat_id=channel_id,
            # ADR-D — a Slack IM (direct message) channel id starts with "D";
            # only there does bare-name vocative routing apply. Public/private
            # channels (C…/G…) stay @Name-only to avoid human-name hijack.
            is_direct=isinstance(channel_id, str) and channel_id.startswith("D"),
        )
        # Record the session→target map (Phase B) + single-terminal fallback for
        # best-effort callers that carry no explicit target.
        self._targets[session_id] = channel_id
        self._last_target = channel_id
        # Per-TURN thread (F011): stamp this turn's originating thread under its
        # trace_id so its reply threads correctly even after a newer concurrent
        # event for the same channel arrives. ``None`` (a DM) records no entry so
        # the reply goes to the channel root. This map is the AUTHORITATIVE source
        # for the on-turn send() path.
        if thread_ts is not None:
            self._put_bounded(self._thread_by_trace, trace_id, thread_ts)
        # Per-CHANNEL thread map: kept for OUT-OF-BAND callers (send_file during a
        # turn, consent/clarify prompts) that resolve by channel id, not trace.
        # Best-effort/latest-wins by design for those paths; the global
        # ``_last_thread`` fallback (the F011 mis-thread hazard) is removed.
        if thread_ts is not None:
            self._threads[channel_id] = thread_ts
        else:
            self._threads.pop(channel_id, None)
        # Per-TURN inbound files (F010): key by trace_id so a later same-user
        # event can't clear them. An event with no files records no entry.
        if file_ids:
            self._put_bounded(self._inbound_files, trace_id, file_ids)
        await self._queue.put(msg)
        log.slack.debug(
            "[slack] adapter.handle_event: exit — queued",
            extra={"_fields": {"trace_id": trace_id, "queue_size": self._queue.qsize()}},
        )

    async def health_check(self) -> HealthStatus:
        """Return ok if a recent ping arrived, otherwise degraded."""
        start = time.perf_counter()
        last = self._last_ping_at
        if last is None:
            latency = (time.perf_counter() - start) * 1000.0
            return HealthStatus(
                name=self.contributor_name,
                status="degraded",
                message="no Slack ping recorded yet",
                latency_ms=latency,
            )
        age_s = (datetime.now(tz=UTC) - last).total_seconds()
        latency = (time.perf_counter() - start) * 1000.0
        if age_s > _HEALTH_STALE_AFTER_S:
            return HealthStatus(
                name=self.contributor_name,
                status="degraded",
                message=f"last ping {age_s:.0f}s ago",
                latency_ms=latency,
            )
        return HealthStatus(
            name=self.contributor_name,
            status="ok",
            message=f"connected — last ping {age_s:.0f}s ago",
            latency_ms=latency,
        )

    # ------------------------------------------------------------------ ADR-6 HealableResource protocol

    @property
    def available(self) -> bool:
        """True if the Slack Bolt app is live and a recent ping was received.

        ponytail: bare cached-state read, deliberately unlogged — matches every
        other HealableResource implementer in this codebase (EmbeddingRegistry,
        LanceDBAdapter, KuzuAdapter, DbPool, DiscordChannelAdapter: all bare
        `available` properties with no I/O). Called on every health-sweep tick
        and from `ensure_available()` itself; logging a hot-path property read
        would be noise, not signal. The state-changing path (`ensure_available()`)
        carries full 4-point logging.
        """
        if self._app is None:
            return False
        if self._last_ping_at is None:
            return False
        now = datetime.now(tz=UTC)
        age_s = (now - self._last_ping_at).total_seconds()
        return age_s <= _HEALTH_STALE_AFTER_S

    @property
    def unavailable_reason(self) -> str | None:
        """Return the degradation message if unhealthy, else None."""
        # 1. ENTRY — implicit (property access)
        if self.available:
            return None
        # 2. DECISION — derive reason (app is None or ping is stale)
        if self._app is None:
            reason = "no Slack Bolt app — channel not started"
        elif self._last_ping_at is None:
            reason = "no Slack ping recorded yet"
        else:
            now = datetime.now(tz=UTC)
            age_s = (now - self._last_ping_at).total_seconds()
            reason = f"last ping {age_s:.0f}s ago (stale beyond {_HEALTH_STALE_AFTER_S:.0f}s)"
        log.slack.debug(
            "[slack] adapter.unavailable_reason: exit",
            extra={"_fields": {"reason": reason}},
        )
        # 3. STEP — return the message
        return reason

    async def ensure_available(self) -> None:
        """Recover a degraded adapter by rebuilding the socket-mode connection.

        Triggers a real reconnect when _last_ping_at exceeds
        _HEALTH_STALE_AFTER_S (reusing the exact threshold health_check()
        already uses to report degraded). The reconnect itself is delegated to
        the callback injected via :meth:`set_reconnector` — the production
        runner owns the live ``AsyncSocketModeHandler`` and rebuilds it there;
        this adapter has no direct ``slack_bolt`` dependency. ``start()`` does
        NOT perform this reconnect (it does no network I/O by design — see its
        docstring), so it is deliberately not called here.

        No reconnector attached (e.g. an adapter constructed outside the full
        orchestrator boot path, such as in isolated unit tests) → a logged
        no-op, never a crash.
        """
        # 1. ENTRY
        log.slack.debug(
            "[slack] adapter.ensure_available: entry",
            extra={"_fields": {"available": self.available, "has_reconnector": self._reconnector is not None}},
        )
        # 2. DECISION — no-op if already healthy
        if self.available:
            log.slack.debug(
                "[slack] adapter.ensure_available: already healthy — no-op"
            )
            return
        if self._reconnector is None:
            log.slack.warning(
                "[slack] adapter.ensure_available: no reconnector attached — cannot heal"
            )
            return
        # 3. STEP — invoke the injected reconnect callback (rebuilds the live socket)
        await self._reconnector()
        # 4. EXIT
        log.slack.info(
            "[slack] adapter.ensure_available: exit — reconnect callback invoked"
        )

    def register_on_recycled(self, cb: Callable[[], None]) -> None:
        """No-op: the adapter's state is not cached downstream.

        Every caller re-acquires the adapter via ChannelRegistry or dependency
        injection, so there is no dead ref to clear on recycling. Matches the
        pattern in EmbeddingRegistry, LanceDBAdapter, and DiscordChannelAdapter.
        """
        log.slack.debug(
            "[slack] adapter.register_on_recycled: no-op (no downstream dependents)"
        )

    def register_with_registry(self, registry: ChannelRegistry | None = None) -> None:
        """Register this adapter with the singleton :class:`ChannelRegistry`."""
        from stackowl.channels.registry import ChannelRegistry

        target = registry or ChannelRegistry.instance()
        log.slack.debug(
            "[slack] adapter.register_with_registry: entry",
            extra={"_fields": {"channel": self.channel_name}},
        )
        target.register(self)
        log.slack.info(
            "[slack] adapter.register_with_registry: exit",
            extra={"_fields": {"channel": self.channel_name}},
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    async def _post_text(
        self,
        text: str,
        *,
        index: int,
        channel: str,
        thread_ts: str | None = None,
        raise_on_error: bool = True,
    ) -> None:
        """Send a single message part via the attached Bolt app, if any.

        ``channel`` is the resolved Slack destination (channel id) — replacing
        the former hardcoded ``"@stackowl"`` so replies reach the originating
        conversation. ``thread_ts`` (when set) threads the reply under the
        originating message; when ``None`` the reply posts to the channel root
        (DM path).

        When no app is attached (unit test path) we only log — the
        TestModeGuard above already protected against accidental live I/O.

        Transport honesty (F-64): on the ON-TURN reply path (the default,
        ``raise_on_error=True``) a ``chat_postMessage`` failure is NOT swallowed
        — it is re-raised as ``DeliveryError("slack", "transport_error")`` so the
        deliverer records ``failed`` and can retry, instead of logging a clean
        send while the user's reply never arrives. A best-effort/proactive caller
        may pass ``raise_on_error=False`` to keep the old log-and-swallow
        behaviour. On a Slack ``ratelimited`` error we retry once before
        surfacing.
        """
        msg_id = uuid.uuid4().hex[:12]
        log.slack.debug(
            "[slack] adapter._post_text: entry",
            extra={
                "_fields": {
                    "msg_id": msg_id,
                    "part_index": index,
                    "len": len(text),
                    "channel": channel,
                    "threaded": thread_ts is not None,
                }
            },
        )
        if self._app is None:
            log.slack.debug(
                "[slack] adapter._post_text: no Bolt app attached — skip",
                extra={"_fields": {"msg_id": msg_id}},
            )
            return
        # The live AsyncApp exposes `.client.chat_postMessage(...)`. We avoid
        # importing slack_sdk types here so the unit tests don't need the
        # package installed; the production runner is responsible for wiring.
        client = getattr(self._app, "client", None)
        if client is None:
            log.slack.warning(
                "[slack] adapter._post_text: attached app has no client",
                extra={"_fields": {"msg_id": msg_id}},
            )
            return
        post_kwargs: dict[str, object] = {"channel": channel, "text": text}
        if thread_ts is not None:
            post_kwargs["thread_ts"] = thread_ts
        try:
            await client.chat_postMessage(**post_kwargs)
            log.slack.debug(
                "[slack] adapter._post_text: exit posted",
                extra={"_fields": {"msg_id": msg_id, "channel": channel}},
            )
        except Exception as err:  # noqa: BLE001
            # Bounded retry-once on a transient Slack rate-limit before deciding
            # whether to surface. Slack-specific error names are acceptable in
            # this thin adapter only.
            if raise_on_error and _is_ratelimited(err):
                log.slack.warning(
                    "[slack] adapter._post_text: ratelimited — retrying once",
                    extra={"_fields": {"msg_id": msg_id}},
                )
                try:
                    await client.chat_postMessage(**post_kwargs)
                    log.slack.debug(
                        "[slack] adapter._post_text: exit posted (after retry)",
                        extra={"_fields": {"msg_id": msg_id, "channel": channel}},
                    )
                    return
                except Exception as retry_err:  # noqa: BLE001
                    err = retry_err
            log.slack.error(
                "[slack] adapter._post_text: post failed",
                exc_info=err,
                extra={"_fields": {"msg_id": msg_id}},
            )
            # F-64: on the on-turn path, a swallowed transport failure means the
            # user's reply silently never arrives. Re-raise so the deliverer
            # records ``failed`` and can retry. Carry only the coarse channel +
            # reason — never the raw channel id / token (sensitive-data mandate).
            if raise_on_error:
                raise DeliveryError("slack", "transport_error") from err

"""GatewayScanner — route incoming messages to the correct handler.

Priority order (ARCH-98 backpressure; ARCH-98: IngressQueue maxsize=3):
1. /panic or !panic anywhere → panic route (FR94)
2. @OwlName at start (NFC-normalised, Unicode word chars, RTL-stripped) → owl route
   - Exact match against :class:`OwlRegistry` when supplied.
   - Otherwise fuzzy-suggest via :class:`FuzzyMatcher`; falls back to secretary.
3. /command at start → command route
4. default → owl route to "secretary"
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from stackowl.exceptions import OwlNotFoundError
from stackowl.infra.observability import log
from stackowl.owls.router import FuzzyMatcher

if TYPE_CHECKING:
    from stackowl.owls.registry import OwlRegistry

_PANIC_RE = re.compile(r"(?:/panic|!panic)", re.IGNORECASE | re.UNICODE)
_AT_OWL_RE = re.compile(r"^@(\w+)", re.UNICODE)
# Leading whitespace is tolerated so that `' /help'` and `'\t/help'` still
# route as commands instead of silently falling through to the secretary LLM.
_SLASH_CMD_RE = re.compile(r"^\s*/(\w+)", re.UNICODE)
_MULTI_AT_OWL_RE = re.compile(r"@(\w+)", re.UNICODE)

_INGRESS_MAXSIZE = 3

_FUZZY_THRESHOLD = 0.8
_FUZZY_MAX_DISTANCE = 2


@dataclass(frozen=True)
class IngressMessage:
    """A raw incoming message before routing."""

    text: str
    session_id: str
    channel: str
    trace_id: str
    # Per-message delivery target for fan-out channels (e.g. a Telegram chat_id).
    # Telegram's ``_handle_update`` stamps the originating chat here so a turn's
    # response routes back to ITS OWN chat under concurrency — never the shared
    # ``_last_chat_id`` (overwritten by every newer inbound update). Single-terminal
    # channels (CLI) leave it None; the adapter then resolves the destination
    # itself. Defaulted so every existing ``IngressMessage(...)`` constructor is
    # byte-for-byte unaffected.
    # String targets are for Slack (channel id / thread_ts); int for Telegram chat_id.
    chat_id: int | str | None = None
    # STEER-1/F060 — a STRUCTURAL reply-to-the-bot link. Telegram's
    # ``_handle_update`` sets this True when ``message.reply_to_message`` points at
    # one of the bot's own messages; the orchestrator turns it into a
    # reply-to-inflight STEER (``parse_explicit_signal``) ONLY when a turn is
    # in-flight for the session (see ``resolve_reply_to_inflight``). A
    # language-neutral structural signal — replying to the running turn is an
    # unambiguous "this refines THAT turn". Defaulted False so every existing
    # ``IngressMessage(...)`` constructor is byte-for-byte unaffected; channels
    # without a reply concept (CLI) leave it False.
    is_reply: bool = False


@dataclass(frozen=True)
class RouteDecision:
    """The routing outcome for an IngressMessage.

    ``suggestion`` carries a human-readable hint when the scanner inferred a
    fuzzy correction. ``stripped_text`` carries the message body after the
    ``@OwlName`` prefix has been removed (whitespace-trimmed) when the route
    targets a specific owl.
    """

    route: Literal["panic", "owl", "command", "parliament"]
    target: str
    suggestion: str | None = None
    stripped_text: str | None = None
    parliament_owls: list[str] | None = None


class IngressQueue:
    """Bounded asyncio queue for raw incoming messages (ARCH-98 backpressure)."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[IngressMessage] = asyncio.Queue(maxsize=_INGRESS_MAXSIZE)

    async def put(self, msg: IngressMessage) -> bool:
        """Enqueue msg. Returns False (drops msg) if the queue is full."""
        try:
            self._queue.put_nowait(msg)
            return True
        except asyncio.QueueFull:
            log.gateway.warning(
                "[gateway] ingress: queue full — dropping message",
                extra={"_fields": {"session_id": msg.session_id, "maxsize": _INGRESS_MAXSIZE}},
            )
            return False

    async def get(self) -> IngressMessage:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    def qsize(self) -> int:
        return self._queue.qsize()


def _strip_rtl(text: str) -> str:
    """Remove Unicode right-to-left marks (U+200F, U+200E)."""
    return text.replace("‏", "").replace("‎", "")


def _strip_at_prefix(text: str, match: re.Match[str]) -> str:
    """Return text with the leading ``@OwlName`` token removed and trimmed."""
    return text[match.end() :].lstrip()


class GatewayScanner:
    """Routes IngressMessage → RouteDecision using priority rules.

    When an :class:`OwlRegistry` is supplied, ``@OwlName`` mentions are
    validated and fuzzy-matched against registered owls.
    """

    def __init__(self, owl_registry: OwlRegistry | None = None) -> None:
        self._owl_registry: OwlRegistry | None = owl_registry
        self._fuzzy: FuzzyMatcher = FuzzyMatcher()

    def _resolve_owl(self, requested: str) -> tuple[str, str | None]:
        """Resolve a requested @OwlName to a registered owl name.

        Returns ``(target, suggestion)`` where ``target`` is the route target
        (a registered owl name or the secretary fallback) and ``suggestion``
        is an optional message describing a fuzzy correction.
        """
        registry = self._owl_registry
        if registry is None:
            return requested, None

        try:
            registry.get(requested)
            return requested, None
        except OwlNotFoundError as exc:
            log.gateway.warning(
                "[gateway] scanner: @OwlName not in registry — attempting fuzzy lookup",
                exc_info=exc,
                extra={"_fields": {"requested": requested}},
            )

        known = [m.name for m in registry.list()]
        match = self._fuzzy.find(
            requested,
            known,
            threshold=_FUZZY_THRESHOLD,
            max_distance=_FUZZY_MAX_DISTANCE,
        )
        if match is not None:
            best, confidence = match
            suggestion = f"Did you mean @{best}? (confidence={confidence:.2f}) — routing your message to @{best}."
            log.gateway.info(
                "[gateway] scanner: fuzzy-matched @%s → @%s",
                requested,
                best,
                extra={
                    "_fields": {
                        "requested": requested,
                        "matched": best,
                        "confidence": confidence,
                    }
                },
            )
            return best, suggestion

        log.gateway.warning(
            "[gateway] scanner: unknown @%s — no fuzzy match — routing to secretary",
            requested,
            extra={"_fields": {"requested": requested}},
        )
        return "secretary", (f"Owl '@{requested}' is not registered — routing to @secretary.")

    def scan(self, msg: IngressMessage) -> RouteDecision:
        log.gateway.info(
            "[gateway] scanner.scan: entry",
            extra={"_fields": {"session_id": msg.session_id, "text_len": len(msg.text)}},
        )
        text = _strip_rtl(unicodedata.normalize("NFC", msg.text))

        if _PANIC_RE.search(text):
            log.gateway.info(
                "[gateway] scanner.scan: panic route",
                extra={"_fields": {"session_id": msg.session_id}},
            )
            return RouteDecision(route="panic", target="panic")

        # Multi-owl detection: 2+ @mentions → mini-parliament
        all_mentions = _MULTI_AT_OWL_RE.findall(text)
        if len(all_mentions) >= 2:
            stripped = _MULTI_AT_OWL_RE.sub("", text).strip()
            log.gateway.info(
                "[gateway] scanner.scan: parliament route (multi-owl)",
                extra={
                    "_fields": {
                        "session_id": msg.session_id,
                        "owls": all_mentions,
                    }
                },
            )
            return RouteDecision(
                route="parliament",
                target="parliament",
                parliament_owls=all_mentions,
                stripped_text=stripped,
            )

        m = _AT_OWL_RE.match(text)
        if m:
            requested_name = m.group(1)
            stripped = _strip_at_prefix(text, m)
            target, suggestion = self._resolve_owl(requested_name)
            log.gateway.info(
                "[gateway] scanner.scan: owl route",
                extra={
                    "_fields": {
                        "session_id": msg.session_id,
                        "requested": requested_name,
                        "target": target,
                        "fuzzy_suggestion": bool(suggestion),
                    }
                },
            )
            return RouteDecision(
                route="owl",
                target=target,
                suggestion=suggestion,
                stripped_text=stripped,
            )

        m2 = _SLASH_CMD_RE.match(text)
        if m2:
            cmd = m2.group(1)
            log.gateway.info(
                "[gateway] scanner.scan: command route",
                extra={"_fields": {"session_id": msg.session_id, "cmd": cmd}},
            )
            return RouteDecision(route="command", target=cmd)

        log.gateway.info(
            "[gateway] scanner.scan: default secretary route",
            extra={"_fields": {"session_id": msg.session_id}},
        )
        return RouteDecision(route="owl", target="secretary")

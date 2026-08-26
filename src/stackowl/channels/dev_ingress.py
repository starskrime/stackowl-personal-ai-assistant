"""A guarded developer ingress — inject a turn down the REAL channel path.

Bakir, 2026-08-25: "you can act like a person and ask something to your platform
depending what you want to test and do real calculation and validation of fixes.
Only rule is send message like it received from telegram."

WHY THIS EXISTS RATHER THAN A SCRIPT. Validating a fix needs traffic that takes
the path production takes; a probe that takes a different path validates a path
nobody uses. There was no way in. The mechanisms that reach a running instance
are the PID file (signals only) and the gateway<->core socket — and that socket
is a trap: the GATEWAY binds it and the CORE dials in as a client, so
``_accept_core`` hands any second connector the core's own role and displaces the
live link. I did exactly that on 2026-08-25 and broke delivery for three and a
half minutes. This facility exists so that mistake is never necessary again.

WHERE IT INJECTS. Into the live channel adapter's own inbound queue, which is the
in-tree blessed pattern — ``VoiceConfirmHandler._inject`` already does precisely
this to turn a confirmed voice transcript into a turn. From the queue the message
travels ``receive()`` -> the orchestrator's per-channel loop -> ``turn_client
.submit`` -> ``IngressFrame`` over the real core socket -> ``_handle_ingress``:
the message ledger, the scanner, the clarify pump, and delivery of the reply back
to the real chat. Byte-identical to a message that arrived from the network,
because it IS the same path — the only thing skipped is the network hop.

THE REPLY GOES TO THE REAL CHAT. That is not a side effect to be engineered away;
it is the point. This platform's own rule is that a task is complete when its
outcome reached its DESTINATION, so an injected question whose answer never
reached Telegram would validate nothing about delivery.

THREE GUARDS, AND EACH IS MEASURABLE.

  THE SOCKET IS OWNER-ONLY (0600). The trust boundary is the same one the core
  socket already relies on: anyone who can write this file can already run code
  as this user. No token is invented here, because a second secret to manage is a
  second secret to leak.

  IT CANNOT EXCEED A REAL MESSAGE. The session_key is checked against the very
  same channel allow-list a real update is checked against — `is_authorized`,
  imported, not reimplemented. Two copies of one authorisation rule is how one of
  them silently drifts, and this codebase has paid for that shape already. An
  injected turn is therefore never more privileged than a typed one.

  EVERY INJECTION IS MARKED AT INFO, FOREVER. ``injected: True`` rides the log
  line so that traffic generated for a test can always be told apart from traffic
  the user really sent. Without it, tomorrow's measurement reads my test messages
  as evidence of real use — and this programme has already been fooled by its own
  probes eleven times. INFO, not DEBUG: production runs at INFO, so a DEBUG line
  is no evidence at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from stackowl.gateway.scanner import IngressMessage
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from collections.abc import Callable


class _Feedable(Protocol):
    """The slice of a channel adapter this listener needs.

    Deliberately a Protocol and not the ChannelAdapter ABC: widening the ABC
    would force a ``feed`` onto every adapter, including the ones for which an
    injected turn makes no sense.
    """

    def feed(self, msg: IngressMessage) -> None: ...


@dataclass(frozen=True)
class DevIngressTarget:
    """One channel this listener may inject into, and who may be impersonated."""

    adapter: _Feedable
    #: Returns True when this session_key is one the channel would accept from
    #: the network. Supplied by the wiring so the CHANNEL owns its own rule.
    is_allowed: Callable[[str], bool]


class DevIngressListener:
    """Accepts newline-delimited JSON on an owner-only unix socket.

    One request object per line::

        {"channel": "telegram", "session_key": "72055773",
         "text": "what is the platform doing right now?", "chat_id": 72055773}

    and one reply object per line::

        {"ok": true, "trace_id": "..."}

    The reply is an ACKNOWLEDGEMENT that the turn was accepted onto the channel's
    queue — never the answer. The answer goes where a real message's answer goes.
    """

    def __init__(
        self,
        socket_path: Path,
        targets: dict[str, DevIngressTarget],
    ) -> None:
        self._socket_path = socket_path
        self._targets = targets
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        """Bind the socket, owner-only. Never binds the core socket."""
        # 1. ENTRY
        log.gateway.debug(
            "[dev_ingress] start: entry",
            extra={"_fields": {"path": str(self._socket_path),
                               "channels": sorted(self._targets)}},
        )
        if not self._targets:
            # 2. DECISION — nothing to inject into is not an error, it is a
            # gateway with no channels up. Say so and stay down.
            log.gateway.info(
                "[dev_ingress] start: no injectable channel — listener not started"
            )
            return
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        # A stale socket file from a killed process would make bind() fail with
        # EADDRINUSE even though nothing is listening.
        with contextlib.suppress(FileNotFoundError):
            self._socket_path.unlink()
        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(self._socket_path)
        )
        # 3. STEP — the guard. Narrow the mode AFTER bind, because bind creates
        # the file with the process umask and that is usually 0644.
        os.chmod(self._socket_path, 0o600)
        # 4. EXIT
        log.gateway.info(
            "[dev_ingress] start: listening",
            extra={"_fields": {"path": str(self._socket_path), "mode": "0600",
                               "channels": sorted(self._targets)}},
        )

    async def stop(self) -> None:
        """Close the socket and remove its file."""
        log.gateway.debug("[dev_ingress] stop: entry")
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None
        with contextlib.suppress(FileNotFoundError):
            self._socket_path.unlink()
        log.gateway.info("[dev_ingress] stop: exit")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Serve one connection: a line in, a line out, repeat until EOF."""
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                reply = await self._handle_line(line)
                writer.write((json.dumps(reply) + "\n").encode("utf-8"))
                await writer.drain()
        except Exception as exc:  # B5 — every except logs
            log.gateway.warning(
                "[dev_ingress] _handle_client: connection failed", exc_info=exc
            )
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    async def _handle_line(self, line: bytes) -> dict[str, object]:
        """Validate one request and enqueue it. Returns the reply object."""
        try:
            payload = json.loads(line.decode("utf-8"))
        except Exception as exc:  # B5
            log.gateway.warning("[dev_ingress] bad JSON", exc_info=exc)
            return {"ok": False, "error": "malformed JSON"}
        if not isinstance(payload, dict):
            return {"ok": False, "error": "expected a JSON object"}

        channel = str(payload.get("channel") or "")
        session_key = str(payload.get("session_key") or "")
        text = str(payload.get("text") or "")
        if not text.strip():
            return {"ok": False, "error": "empty text"}

        target = self._targets.get(channel)
        if target is None:
            log.gateway.warning(
                "[dev_ingress] refused — unknown channel",
                extra={"_fields": {"channel": channel,
                                   "known": sorted(self._targets)}},
            )
            return {"ok": False, "error": f"unknown channel {channel!r}"}

        # THE GUARD THAT MATTERS. The channel's own allow-list decides, so an
        # injected turn can never reach further than one that arrived over the
        # network. Refusals are logged at WARNING because a refusal here is
        # either a mistake worth seeing or an attempt worth seeing.
        if not target.is_allowed(session_key):
            log.gateway.warning(
                "[dev_ingress] REFUSED — session_key is not on the channel "
                "allow-list; an injected turn may never exceed a real one",
                extra={"_fields": {"channel": channel, "session_key": session_key}},
            )
            return {"ok": False, "error": "session_key not authorised for this channel"}

        # `bool` is excluded explicitly because it is a subclass of int in
        # Python, and a JSON `true` coerced to chat_id 1 would deliver the reply
        # to whatever chat happens to be numbered 1.
        raw_chat = payload.get("chat_id")
        chat_id: int | str | None = (
            raw_chat
            if isinstance(raw_chat, int | str) and not isinstance(raw_chat, bool)
            else None
        )

        msg = IngressMessage(
            text=text,
            session_key=session_key,
            channel=channel,
            trace_id=uuid4().hex,
            chat_id=chat_id,
            is_reply=bool(payload.get("is_reply", False)),
            is_direct=bool(payload.get("is_direct", True)),
        )
        target.adapter.feed(msg)
        # INFO and PERMANENT. `injected` is what lets a later measurement tell
        # test traffic from real traffic — without it this facility quietly
        # corrupts the evidence base it exists to serve.
        log.gateway.info(
            "[dev_ingress] injected a turn onto the live channel queue",
            extra={"_fields": {
                "injected": True,
                "channel": channel,
                "session_key": session_key,
                "trace_id": msg.trace_id,
                "chat_id": chat_id,
                "text_len": len(text),
            }},
        )
        return {"ok": True, "trace_id": msg.trace_id}

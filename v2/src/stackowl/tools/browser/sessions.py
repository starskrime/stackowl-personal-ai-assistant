"""BrowserSessionRegistry — per-owner, TTL-evicted browser session handles.

A "session" wraps one Playwright BrowserContext plus a dict of Pages keyed by
a page_handle string. The LLM gets a session_id from any tool that needs to
preserve state across calls (browser_navigate, browser_browse) and threads
it through subsequent tool calls in the same conversation turn.

owner_key namespacing keeps Telegram users isolated from each other:
- CLI:      owner_key = "local"
- Telegram: owner_key = f"telegram:{chat_id}"
- WhatsApp: owner_key = f"whatsapp:{jid}"
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from stackowl.config.browser import BrowserSettings, ProxyConfig
from stackowl.infra.observability import log
from stackowl.tools.browser.runtime import CamoufoxRuntime

# Per-page ring-buffer bound. Events fire whether or not a tool is reading; the
# buffer is bounded (drop-oldest) so an attacker page logging in a loop can't OOM
# us. Per-page (not per-session) so navigations/tabs don't mix or leak (party
# Operations §2).
_PAGE_LOG_BUFFER_MAX = 500
# Bound on simultaneously-pending JS dialogs per page — an attacker page firing
# alert() in a loop must not OOM us. When exceeded, the oldest is auto-dismissed.
_DIALOG_QUEUE_MAX = 20


@dataclass
class PendingDialog:
    """A JS dialog awaiting accept/dismiss, captured by ``page.on("dialog")``."""

    dialog_id: str
    type: str  # alert | confirm | prompt | beforeunload
    message: str
    default_value: str
    dialog: Any  # the engine Dialog handle (accept()/dismiss() are async)
    created_at: float
    auto_task: Any = None  # asyncio.Task for the TTL auto-dismiss (cancelled on resolve)


@dataclass
class PageObservers:
    """Bounded, eagerly-filled buffers for one page's console/error/dialog events.

    Wired at page construction (not first tool call) so events emitted before any
    tool call are still captured (party Operations §1). One holder per page_handle.
    """

    console: deque[dict[str, str]] = field(
        default_factory=lambda: deque(maxlen=_PAGE_LOG_BUFFER_MAX)
    )
    errors: deque[dict[str, str]] = field(
        default_factory=lambda: deque(maxlen=_PAGE_LOG_BUFFER_MAX)
    )
    # Pending JS dialogs keyed by dialog_id (insertion-ordered → oldest first).
    dialogs: dict[str, PendingDialog] = field(default_factory=dict)
    # F160 — per-handler-kind failure counters. A hostile page that fires events
    # faster than the handler can absorb them (or whose payloads trip the handler
    # repeatedly) must NOT flood the error log with one ERROR per event. We log the
    # FIRST failure of each kind loudly (so a real handler bug stays visible), then
    # suppress to DEBUG with a running count.
    handler_failures: dict[str, int] = field(default_factory=dict)


# F160 — after this many failures of one handler kind on a page, drop from
# loud (ERROR) to suppressed (DEBUG) logging so a hostile page that trips a
# handler on every event cannot flood the error log. Re-emits a running count
# at each power-of-the-window boundary so persistent breakage stays observable.
_HANDLER_FAILURE_LOUD_LIMIT = 1
_HANDLER_FAILURE_RESAMPLE_EVERY = 1000


def _log_handler_failure(obs: PageObservers, kind: str, handle: str, exc: Exception) -> None:
    """Rate-limit per-page page-observer handler-failure logging (F160).

    The FIRST failure of each ``kind`` on this page logs at ERROR (a real handler
    bug stays visible); subsequent failures suppress to DEBUG, with a periodic
    ERROR re-emit carrying the running count so chronic breakage is not silently
    lost. Never raises — a logging failure must not break the page event loop.
    """
    count = obs.handler_failures.get(kind, 0) + 1
    obs.handler_failures[kind] = count
    if count <= _HANDLER_FAILURE_LOUD_LIMIT or count % _HANDLER_FAILURE_RESAMPLE_EVERY == 0:
        log.engine.error(
            f"[browser] {kind} handler failed",
            exc_info=exc,
            extra={"_fields": {"page_handle": handle, "kind": kind, "failure_count": count}},
        )
    else:
        log.engine.debug(
            f"[browser] {kind} handler failed (suppressed — see first failure)",
            extra={"_fields": {"page_handle": handle, "kind": kind, "failure_count": count}},
        )


class BrowserSessionLimitError(Exception):
    """Raised when global session cap is exceeded."""


class BrowserSessionNotFoundError(KeyError):
    """Raised when a session_id is unknown or has been evicted."""


@dataclass
class BrowserSession:
    session_id: str
    owner_key: str
    profile_name: str | None
    context: Any  # Playwright BrowserContext
    pages: dict[str, Any] = field(default_factory=dict)
    # Per-page observers (console/error buffers, …), keyed by page_handle.
    observers: dict[str, PageObservers] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)
    nav_count: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def touch(self) -> None:
        self.last_activity = time.monotonic()


@dataclass
class BrowserSessionInfo:
    session_id: str
    owner_key: str
    profile_name: str | None
    age_seconds: float
    last_url_path: str | None
    page_count: int


class BrowserSessionRegistry:
    """Tracks open browser sessions, enforces caps, evicts idle ones."""

    def __init__(self, runtime: CamoufoxRuntime, settings: BrowserSettings) -> None:
        self._runtime = runtime
        self._settings = settings
        self._sessions: dict[str, BrowserSession] = {}
        # F155 — in-flight reservations counted toward the cap under
        # ``_registry_lock`` to close the open() check-then-act (TOCTOU) window:
        # reserve a slot BEFORE the awaited open_context, commit or roll back on
        # EVERY exit. ``_recycle_gen`` bumps on each runtime recycle; an open that
        # captures a stale generation at reserve time refuses to register the now-
        # dead context at commit time (recycle-during-open guard).
        self._reserved: int = 0
        self._recycle_gen: int = 0
        # Strong refs to fire-and-forget background tasks (oldest-dialog dismiss),
        # so they are not GC'd mid-flight. Discarded on completion.
        self._bg_tasks: set[asyncio.Task[None]] = set()
        self._registry_lock = asyncio.Lock()
        self._sweep_task: asyncio.Task[None] | None = None
        # Self-healing: when runtime is recycled (crash or scheduled), all
        # stored BrowserContext refs become dead. Purge them so the next
        # caller gets a clean error and can reopen.
        if hasattr(runtime, "register_on_recycled"):
            runtime.register_on_recycled(self._purge_all_dead_sessions)
        log.engine.debug(
            "[browser] sessions.init: ready",
            extra={"_fields": {
                "max_sessions": settings.max_concurrent_sessions,
                "idle_timeout_min": settings.session_idle_timeout_minutes,
            }},
        )

    def _purge_all_dead_sessions(self) -> None:
        """Sync: drop all stored sessions after a runtime recycle.

        The underlying BrowserContext refs are dead — awaiting close() on
        them would hang or raise. We just drop them and let GC reap.
        """
        # Bump the recycle generation FIRST so any open() in flight (parked at the
        # awaited open_context) sees the mismatch at commit time and refuses to
        # register its now-dead context (F155 recycle-during-open guard).
        self._recycle_gen += 1
        if not self._sessions:
            return
        count = len(self._sessions)
        for sess in self._sessions.values():
            self._cancel_session_timers(sess)
        self._sessions.clear()
        log.engine.warning(
            "[browser] sessions.purge: dropped all sessions after runtime recycle",
            extra={"_fields": {"dropped": count}},
        )

    async def start_sweep_loop(self) -> None:
        """Start the periodic TTL eviction sweep."""
        if self._sweep_task is not None:
            return
        self._sweep_task = asyncio.create_task(self._sweep_loop(), name="browser_session_sweep")
        log.engine.info("[browser] sessions.sweep: loop started")

    async def stop_sweep_loop(self) -> None:
        if self._sweep_task is None:
            return
        self._sweep_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._sweep_task
        self._sweep_task = None
        log.engine.info("[browser] sessions.sweep: loop stopped")

    async def _sweep_loop(self) -> None:
        interval = 300.0  # sweep every 5 min
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    evicted = await self.evict_idle()
                    if evicted:
                        log.engine.info(
                            "[browser] sessions.sweep: evicted idle",
                            extra={"_fields": {"count": evicted}},
                        )
                except Exception as exc:
                    log.engine.error("[browser] sessions.sweep: failed", exc_info=exc)
        except asyncio.CancelledError:
            raise

    async def evict_idle(self) -> int:
        """Close sessions idle longer than session_idle_timeout_minutes. Returns eviction count."""
        idle_secs = self._settings.session_idle_timeout_minutes * 60.0
        now = time.monotonic()
        to_evict: list[str] = []
        async with self._registry_lock:
            for sid, sess in self._sessions.items():
                if (now - sess.last_activity) >= idle_secs:
                    to_evict.append(sid)
        for sid in to_evict:
            await self.close(sid)
        return len(to_evict)

    async def open(
        self,
        owner_key: str,
        *,
        profile_name: str | None = None,
        proxy: ProxyConfig | None = None,
    ) -> str:
        """Allocate a new BrowserSession; returns the session_id.

        The reservation lifetime is strictly within this call: a slot is reserved
        under the lock (counting live sessions + in-flight reservations, so
        concurrent opens cannot all pass the cap-read), the heavy ``open_context``
        runs outside the lock, then the session is committed (or the reservation
        rolled back) under the lock. ``evict_idle``/``close`` only remove COMMITTED
        sessions — never reservations.
        """
        log.engine.debug(
            "[browser] sessions.open: entry",
            extra={"_fields": {"owner_key": owner_key, "profile_name": profile_name}},
        )
        # Check-and-RESERVE atomically (F155 TOCTOU close). Capture the recycle
        # generation so a runtime recycle mid-open is detected at commit time.
        async with self._registry_lock:
            if len(self._sessions) + self._reserved >= self._settings.max_concurrent_sessions:
                raise BrowserSessionLimitError(
                    f"max concurrent browser sessions reached ({self._settings.max_concurrent_sessions})"
                )
            self._reserved += 1
            gen = self._recycle_gen
            log.engine.debug(
                "[browser] sessions.open: slot reserved",
                extra={"_fields": {"reserved": self._reserved, "gen": gen}},
            )
        # The reservation MUST release on EVERY exit (commit / open-fail / recycle /
        # cancel) or the cap shrinks into refuse-everything (leak-DOWN).
        committed = False
        try:
            ctx = await self._runtime.open_context(
                owner_key=owner_key, profile_name=profile_name, proxy=proxy,
            )
            session_id = uuid.uuid4().hex
            sess = BrowserSession(
                session_id=session_id,
                owner_key=owner_key,
                profile_name=profile_name,
                context=ctx,
            )
            # COMMIT under the lock, re-validating the recycle generation.
            async with self._registry_lock:
                if self._recycle_gen != gen:
                    # The runtime was recycled mid-open: this context is already
                    # dead. Do NOT register it; close it and surface loudly (no
                    # silent swallow, no fake session).
                    log.engine.warning(
                        "[browser] sessions.open: runtime recycled mid-open — discarding context",
                        extra={"_fields": {"owner_key": owner_key,
                                           "captured_gen": gen, "now_gen": self._recycle_gen}},
                    )
                    with contextlib.suppress(Exception):
                        await ctx.close()
                    raise BrowserSessionLimitError(
                        "browser runtime recycled during session open; retry"
                    )
                self._sessions[session_id] = sess
                self._reserved -= 1
                committed = True
            log.engine.info(
                "[browser] sessions.open: exit",
                extra={"_fields": {
                    "session_id": session_id,
                    "owner_key": owner_key,
                    "profile_name": profile_name,
                    "active_sessions": len(self._sessions),
                }},
            )
            return session_id
        finally:
            if not committed:
                # Roll back on open-fail / recycle-discard / cancel. The
                # open_context exception itself propagates (it is not caught here).
                async with self._registry_lock:
                    self._reserved -= 1
                    rolled = self._reserved
                log.engine.debug(
                    "[browser] sessions.open: reservation rolled back",
                    extra={"_fields": {"reserved": rolled}},
                )

    async def get(self, session_id: str) -> BrowserSession:
        sess = self._sessions.get(session_id)
        if sess is None:
            raise BrowserSessionNotFoundError(f"browser session not found: {session_id}")
        sess.touch()
        return sess

    async def get_page(self, session_id: str, page_handle: str | None = None) -> tuple[BrowserSession, Any, str]:
        """Return (session, page, page_handle). Creates a new Page if no handle given.

        Enforces max_concurrent_pages_per_session.
        """
        sess = await self.get(session_id)
        async with sess.lock:
            if page_handle is not None and page_handle in sess.pages:
                return sess, sess.pages[page_handle], page_handle
            if len(sess.pages) >= self._settings.max_concurrent_pages_per_session:
                raise BrowserSessionLimitError(
                    f"max pages per session reached ({self._settings.max_concurrent_pages_per_session})"
                )
            page = await sess.context.new_page()
            handle = page_handle or uuid.uuid4().hex[:8]
            sess.pages[handle] = page
            self._wire_page_observers(sess, page, handle)
            sess.touch()
            log.engine.debug(
                "[browser] sessions.get_page: new",
                extra={"_fields": {"session_id": session_id, "page_handle": handle}},
            )
            return sess, page, handle

    def _wire_page_observers(self, sess: BrowserSession, page: Any, handle: str) -> None:
        """Eagerly attach console/error buffers to a freshly-created page.

        Handlers are sync (Playwright requirement) and fill bounded per-page
        ring buffers from page birth, so logs emitted before the first
        ``browser_console`` call are not lost. Best-effort: a stubbed page in
        tests may not support ``.on`` — we still register the buffer so reads
        return empty arrays rather than erroring.
        """
        obs = PageObservers()
        sess.observers[handle] = obs
        if not hasattr(page, "on"):
            return

        def _on_console(msg: Any) -> None:
            try:
                obs.console.append({
                    "type": str(getattr(msg, "type", "log")),
                    "text": str(getattr(msg, "text", "")),
                })
            except Exception as exc:  # never let a log handler break the page
                _log_handler_failure(obs, "console", handle, exc)

        def _on_pageerror(err: Any) -> None:
            try:
                # Capture the error class too — for an LLM debugging a page the
                # type (TypeError vs ReferenceError) is high-signal.
                obs.errors.append({
                    "name": str(getattr(err, "name", "") or ""),
                    "message": str(getattr(err, "message", None) or err),
                })
            except Exception as exc:
                _log_handler_failure(obs, "pageerror", handle, exc)

        def _on_dialog(dialog: Any) -> None:
            try:
                self._register_dialog(obs, dialog)
            except Exception as exc:
                _log_handler_failure(obs, "dialog", handle, exc)

        try:
            page.on("console", _on_console)
            page.on("pageerror", _on_pageerror)
            page.on("dialog", _on_dialog)
        except Exception as exc:  # stubbed/limited page object — degrade, but log
            log.engine.debug(
                "[browser] sessions.get_page: observer wiring skipped",
                extra={"_fields": {"page_handle": handle, "exc": str(exc)}},
            )

    def _register_dialog(self, obs: PageObservers, dialog: Any) -> None:
        """Record a pending dialog and arm its TTL auto-dismiss (self-healing).

        A JS dialog blocks the page until accepted/dismissed; if no action arrives
        within ``dialog_auto_dismiss_seconds`` we dismiss it so the page never
        hangs. The queue is bounded — when full, the oldest pending dialog is
        auto-dismissed to make room (attacker alert()-loop protection).
        """
        if len(obs.dialogs) >= _DIALOG_QUEUE_MAX:
            oldest_id, oldest = next(iter(obs.dialogs.items()))
            obs.dialogs.pop(oldest_id, None)
            if oldest.auto_task is not None:
                oldest.auto_task.cancel()
            self._spawn_bg(self._safe_dismiss(oldest.dialog))
            log.engine.warning(
                "[browser] dialog queue full — auto-dismissed oldest",
                extra={"_fields": {"dropped_id": oldest_id}},
            )
        dialog_id = uuid.uuid4().hex[:8]
        pd = PendingDialog(
            dialog_id=dialog_id,
            type=str(getattr(dialog, "type", "") or ""),
            message=str(getattr(dialog, "message", "") or ""),
            default_value=str(getattr(dialog, "default_value", "") or ""),
            dialog=dialog,
            created_at=time.monotonic(),
        )
        obs.dialogs[dialog_id] = pd
        ttl = self._settings.dialog_auto_dismiss_seconds
        with contextlib.suppress(RuntimeError):  # no running loop (stubbed test page)
            pd.auto_task = asyncio.get_running_loop().create_task(
                self._auto_dismiss_after(obs, dialog_id, ttl)
            )
        log.engine.debug(
            "[browser] dialog captured",
            extra={"_fields": {"dialog_id": dialog_id, "type": pd.type}},
        )

    @staticmethod
    async def _safe_dismiss(dialog: Any) -> None:
        try:
            await dialog.dismiss()
        except Exception as exc:
            log.engine.debug(
                "[browser] sessions._safe_dismiss: dialog dismiss failed — page may be gone",
                exc_info=exc,
            )

    def _spawn_bg(self, coro: Any) -> None:
        """Run a fire-and-forget coroutine, holding a strong ref so it isn't GC'd."""
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:  # no running loop (stubbed test page)
            coro.close()
            return
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    @staticmethod
    def _cancel_dialog_timers(obs: PageObservers) -> None:
        """Cancel a page's pending-dialog TTL timers and drop the entries.

        Called on tab/session teardown so leaked timers never fire ``dismiss`` on
        a closed Dialog/page (self-healing teardown, party Operations §3).
        """
        for pd in obs.dialogs.values():
            if pd.auto_task is not None:
                with contextlib.suppress(Exception):
                    pd.auto_task.cancel()
        obs.dialogs.clear()

    def _cancel_session_timers(self, sess: BrowserSession) -> None:
        for obs in sess.observers.values():
            self._cancel_dialog_timers(obs)

    async def _auto_dismiss_after(self, obs: PageObservers, dialog_id: str, ttl: float) -> None:
        try:
            await asyncio.sleep(ttl)
        except asyncio.CancelledError:
            return  # resolved by the tool before TTL — nothing to do
        pd = obs.dialogs.pop(dialog_id, None)
        if pd is None:
            return
        await self._safe_dismiss(pd.dialog)
        log.engine.warning(
            "[browser] dialog auto-dismissed on TTL",
            extra={"_fields": {"dialog_id": dialog_id, "ttl_s": ttl}},
        )

    async def list_for_owner(self, owner_key: str) -> list[BrowserSessionInfo]:
        now = time.monotonic()
        out: list[BrowserSessionInfo] = []
        async with self._registry_lock:
            for sid, sess in self._sessions.items():
                if sess.owner_key != owner_key:
                    continue
                last_url_path: str | None = None
                # Best-effort grab of any current page URL (path only).
                for page in sess.pages.values():
                    try:
                        from urllib.parse import urlparse
                        u = urlparse(page.url)
                        last_url_path = f"{u.scheme}://{u.netloc}{u.path}"
                        break
                    except Exception:
                        continue
                out.append(BrowserSessionInfo(
                    session_id=sid,
                    owner_key=sess.owner_key,
                    profile_name=sess.profile_name,
                    age_seconds=now - sess.created_at,
                    last_url_path=last_url_path,
                    page_count=len(sess.pages),
                ))
        return out

    async def close(self, session_id: str) -> None:
        async with self._registry_lock:
            sess = self._sessions.pop(session_id, None)
        if sess is None:
            return
        log.engine.info(
            "[browser] sessions.close: entry",
            extra={"_fields": {"session_id": session_id, "owner_key": sess.owner_key}},
        )
        # Cancel armed dialog TTL timers so they don't fire against a closed page.
        self._cancel_session_timers(sess)
        with contextlib.suppress(Exception):
            await sess.context.close()
        manager = getattr(sess.context, "_stackowl_persistent_manager", None)
        if manager is not None:
            with contextlib.suppress(Exception):
                await manager.__aexit__(None, None, None)
        log.engine.info(
            "[browser] sessions.close: exit",
            extra={"_fields": {"session_id": session_id, "active_sessions": len(self._sessions)}},
        )

    async def close_all_for_owner(self, owner_key: str) -> int:
        async with self._registry_lock:
            ids = [sid for sid, s in self._sessions.items() if s.owner_key == owner_key]
        for sid in ids:
            await self.close(sid)
        return len(ids)

    async def close_all(self) -> None:
        async with self._registry_lock:
            ids = list(self._sessions.keys())
        for sid in ids:
            await self.close(sid)

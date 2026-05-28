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
from dataclasses import dataclass, field
from typing import Any

from stackowl.config.browser import BrowserSettings, ProxyConfig
from stackowl.infra.observability import log
from stackowl.tools.browser.runtime import CamoufoxRuntime


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
        if not self._sessions:
            return
        count = len(self._sessions)
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
        """Allocate a new BrowserSession; returns the session_id."""
        log.engine.debug(
            "[browser] sessions.open: entry",
            extra={"_fields": {"owner_key": owner_key, "profile_name": profile_name}},
        )
        async with self._registry_lock:
            if len(self._sessions) >= self._settings.max_concurrent_sessions:
                raise BrowserSessionLimitError(
                    f"max concurrent browser sessions reached ({self._settings.max_concurrent_sessions})"
                )

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
        async with self._registry_lock:
            self._sessions[session_id] = sess
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
            sess.touch()
            log.engine.debug(
                "[browser] sessions.get_page: new",
                extra={"_fields": {"session_id": session_id, "page_handle": handle}},
            )
            return sess, page, handle

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

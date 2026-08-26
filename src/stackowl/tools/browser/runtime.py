"""CamoufoxRuntime — process-wide Camoufox browser singleton.

One AsyncCamoufox instance per StackOwl process. All BrowserContexts (sessions
and one-shot fetches) come from this runtime. Handles cold start, recycling
against the Firefox-derivative memory leak (issue #245), and a per-domain
rate-limiter that protects targets from runaway loops.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from stackowl.config.browser import BrowserSettings, ProxyConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log


def _proxy_to_dict(proxy: ProxyConfig) -> dict[str, str]:
    out: dict[str, str] = {"server": proxy.server}
    if proxy.username:
        out["username"] = proxy.username
    if proxy.password:
        out["password"] = proxy.password
    if proxy.bypass:
        out["bypass"] = proxy.bypass
    return out


class CamoufoxRuntime:
    """Owns one AsyncCamoufox process; vends BrowserContexts on demand."""

    def __init__(self, settings: BrowserSettings) -> None:
        self._settings = settings
        self._browser: Any = None
        self._manager: Any = None  # AsyncCamoufox context manager handle
        #: Playwright driver handle, only for backend="attach".
        self._pw: Any = None
        #: True when we are driving a browser over CDP rather than in-process.
        self._attached: bool = False
        #: Set only when the PLATFORM started the browser — then it is ours to
        #: stop. None means the browser was someone else's and must outlive us.
        self._launched: Any = None
        #: Consecutive local-engine open failures. Reset on any success.
        self._local_failures: int = 0
        #: True once we have escalated, so the decision is logged exactly once.
        self._escalated: bool = False
        self._lock = asyncio.Lock()
        self._nav_count = 0
        self._last_nav: float = time.monotonic()
        self._started_at: float | None = None
        self._domain_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._domain_last_nav: dict[str, float] = {}
        self.available: bool = False
        self._unavailable_reason: str | None = None
        self._on_recycled_cbs: list[Callable[[], None]] = []
        self._recycle_count: int = 0
        self._last_recycle_at: float | None = None
        self._last_recycle_reason: str | None = None
        log.engine.debug(
            "[browser] runtime.init: ready",
            extra={"_fields": {"headless": settings.headless_mode, "humanize": settings.humanize}},
        )

    def register_on_recycled(self, cb: Callable[[], None]) -> None:
        """Register a sync callback fired whenever the browser is recycled (crash or scheduled).

        Dependents (e.g. BrowserSessionRegistry) use this to purge dead refs.
        Callback runs inside the recovery path and MUST be sync + side-effect-only.
        """
        self._on_recycled_cbs.append(cb)

    def _fire_on_recycled(self) -> None:
        for cb in self._on_recycled_cbs:
            try:
                cb()
            except Exception as exc:
                log.engine.error(
                    "[browser] runtime.on_recycled: callback failed",
                    exc_info=exc,
                )

    def _mark_disconnected(self) -> None:
        """Sync handler for Playwright's disconnect event. Just flips state + fires callbacks."""
        if not self.available and self._browser is None:
            return  # already known dead
        log.engine.warning(
            "[browser] runtime.disconnect: browser process gone — marking unavailable",
        )
        self.available = False
        self._browser = None
        self._unavailable_reason = "browser process disconnected"
        self._fire_on_recycled()

    def _attach_disconnect_listener(self) -> None:
        if self._browser is None:
            return
        try:
            self._browser.on("disconnected", lambda _b: self._mark_disconnected())
        except Exception as exc:  # Stubbed Browser in tests may not support on()
            log.engine.debug(
                "[browser] runtime.attach_disconnect: skipped",
                extra={"_fields": {"exc": str(exc)}},
            )

    @property
    def cold_start_ms(self) -> float | None:
        return None if self._started_at is None else (time.monotonic() - self._started_at) * 1000.0

    @property
    def settings(self) -> BrowserSettings:
        return self._settings

    @property
    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason

    @property
    def recycle_count(self) -> int:
        return self._recycle_count

    @property
    def last_recycle_at(self) -> float | None:
        return self._last_recycle_at

    @property
    def last_recycle_reason(self) -> str | None:
        return self._last_recycle_reason

    def _kwargs_from_settings(self) -> dict[str, Any]:
        s = self._settings
        kwargs: dict[str, Any] = {
            "headless": s.headless_mode if s.headless_mode != "true" else True,
            "humanize": s.humanize,
            "block_images": s.block_images,
            "block_webrtc": s.block_webrtc,
            "geoip": s.geoip,
            "disable_coop": s.disable_coop,
            # Suppress Camoufox's LeakWarning about block_images / disable_coop being
            # detectable on sophisticated WAFs. We accept that tradeoff explicitly:
            # default-off block_images would 4x our bandwidth + Jetson load.
            "i_know_what_im_doing": True,
        }
        if s.headless_mode == "false":
            kwargs["headless"] = False
        if s.addons:
            kwargs["addons"] = [str(p) for p in s.addons]
        # Baseline prefs merged under any operator overrides. Camoufox ships with
        # session history effectively disabled, which makes page.go_back() a no-op
        # (browser_back / FF-E2-1) — restore it. Verified: this single pref re-enables
        # back/forward navigation on Camoufox/Firefox 135.
        prefs: dict[str, Any] = {"browser.sessionhistory.max_entries": 50}
        if s.firefox_user_prefs:
            prefs.update(dict(s.firefox_user_prefs))
        kwargs["firefox_user_prefs"] = prefs
        if s.default_proxy is not None:
            kwargs["proxy"] = _proxy_to_dict(s.default_proxy)
        return kwargs

    async def _open_backend(self) -> Any:
        """Open whichever backend is configured. The ONLY place that decides.

        Every path that needs a browser — cold start, recycle, self-heal — comes
        through here, so a backend can never be honoured on one path and ignored
        on another. It was two places for about an hour on 2026-08-26 and the
        attach backend broke on its first recycle.
        """
        backend = self._effective_backend()
        if backend == "managed":
            return await self._open_managed_cdp()
        if backend == "attach":
            return await self._connect_over_cdp()
        # Deferred import — the local engine is optional at install time.
        from camoufox.async_api import AsyncCamoufox

        self._manager = AsyncCamoufox(**self._kwargs_from_settings())  # type: ignore[no-untyped-call]
        return await self._manager.__aenter__()

    #: Consecutive local-engine failures before the platform stops trying it and
    #: provisions a CDP browser instead. Two, not one: a single crash is noise, a
    #: second in a row is a pattern. Measured on this box the real number was 43.
    _LOCAL_FAILURES_BEFORE_ESCALATION = 2

    def _effective_backend(self) -> str:
        """The backend to actually open, which is not always the configured one.

        SELF-HEALING, AND IT IS THE POINT OF THE WHOLE FEATURE. Bakir, 2026-08-26:
        "when we ship it to customer device i will not have access to help." A
        config flag does not help there — someone has to set it. So when the local
        engine fails repeatedly the platform stops asking it again and provisions
        a CDP browser itself.

        It escalates on MEASURED failure, never on a guess, and only in one
        direction. An explicitly configured backend is honoured as written: if the
        operator asked for `attach` or `managed`, that is what they get.
        """
        configured = str(getattr(self._settings, "backend", "local"))
        if configured != "local":
            return configured
        # MEASURE THE EFFECT, NOT THE CALL. The first version of this counted
        # failed OPENS and never fired, because the engine opens perfectly well —
        # measured 2026-08-26 under real traffic: "runtime.recycle: restart ok",
        # then browser_navigate fails anyway. The engine is not failing to start,
        # it is failing to BE USABLE, and the observable for that is how often it
        # had to be recycled. That is the number that hit 43 on this box while
        # every single restart reported success.
        if max(self._local_failures, self._recycle_count) >= self._LOCAL_FAILURES_BEFORE_ESCALATION:
            if not self._escalated:
                self._escalated = True
                # INFO and loud: this is the platform changing its own behaviour,
                # and an operator reading the log later must be able to see that it
                # happened and why, without being here when it did.
                log.engine.info(
                    "[browser] runtime: the local engine failed repeatedly — "
                    "escalating to a platform-provisioned CDP browser",
                    extra={"_fields": {
                        "local_failures": self._local_failures,
                        "threshold": self._LOCAL_FAILURES_BEFORE_ESCALATION,
                    }},
                )
            return "managed"
        return "local"

    async def _open_managed_cdp(self) -> Any:
        """Start our OWN CDP browser and attach to it. No operator required.

        The difference from ``attach`` is ownership, and it is the whole reason
        this exists separately: nobody has to start a browser or put a URL in
        config. The platform provisions one, uses it, and stops it — which is the
        only version of this that works on a device the maintainer cannot reach.

        If no Chromium-family binary exists we fall back to the local engine
        rather than failing outright, so ``managed`` is always at least as good as
        ``local`` and never worse.
        """
        from stackowl.tools.browser.cdp_launcher import launch_cdp_browser

        launched = await launch_cdp_browser()
        if launched is None:
            log.engine.warning(
                "[browser] runtime: managed backend could not provision a CDP "
                "browser — falling back to the local engine"
            )
            from camoufox.async_api import AsyncCamoufox

            self._manager = AsyncCamoufox(**self._kwargs_from_settings())  # type: ignore[no-untyped-call]
            return await self._manager.__aenter__()

        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        browser = await self._pw.chromium.connect_over_cdp(launched.cdp_url)
        # OURS. Teardown stops the process; an `attach` browser never would.
        self._launched = launched
        self._attached = True
        log.engine.info(
            "[browser] runtime.managed: attached to a platform-owned browser",
            extra={"_fields": {"cdp_url": launched.cdp_url, "binary": launched.binary}},
        )
        return browser

    async def _connect_over_cdp(self) -> Any:
        """Attach to a Chromium-family browser already listening on a CDP port.

        THE POINT IS NOT CHROMIUM, IT IS OFF-BOX. `connect_over_cdp` returns the
        same Playwright ``Browser`` the local engine yields, so every existing
        tool, session and snapshot path works against it unchanged — the seam is
        this one method. What changes is WHERE the browser runs: a host that is
        not this one, which is the only thing that actually lifts the constraint
        measured on 2026-08-26 (ten calls, zero successes, an engine that will not
        stay up).

        WE DO NOT OWN THIS BROWSER. It was running before us and must survive us,
        so teardown DISCONNECTS and never closes it — see ``_teardown_inside_lock``.
        """
        url = str(getattr(self._settings, "attach_url", "") or "").strip()
        if not url:
            raise ValueError(
                "browser.backend='attach' requires browser.attach_url "
                "(e.g. http://127.0.0.1:9222)"
            )
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        log.engine.info(
            "[browser] runtime.attach: connecting",
            extra={"_fields": {"cdp_url": url}},
        )
        browser = await self._pw.chromium.connect_over_cdp(url)
        self._attached = True
        log.engine.info(
            "[browser] runtime.attach: connected",
            extra={"_fields": {"cdp_url": url, "contexts": len(browser.contexts)}},
        )
        return browser

    async def start(self) -> None:
        log.engine.info(
            "[browser] runtime.start: entry",
            extra={"_fields": {"headless": self._settings.headless_mode}},
        )
        TestModeGuard.assert_not_test_mode("browser.runtime.start")
        async with self._lock:
            if self._browser is not None:
                log.engine.debug("[browser] runtime.start: already running")
                return
            t0 = time.monotonic()
            try:
                self._browser = await self._open_backend()
                self._local_failures = 0
            except Exception as exc:
                if self._effective_backend() == "local":
                    self._local_failures += 1
                self._unavailable_reason = f"{type(exc).__name__}: {exc}"
                self.available = False
                log.engine.error(
                    "[browser] runtime.start: failed — browser tools unavailable",
                    exc_info=exc,
                    extra={"_fields": {"reason": self._unavailable_reason}},
                )
                return
            self.available = True
            self._started_at = time.monotonic()
            self._nav_count = 0
            self._last_nav = self._started_at
            self._attach_disconnect_listener()
            duration_ms = (self._started_at - t0) * 1000.0
            log.engine.info(
                "[browser] runtime.start: exit",
                extra={"_fields": {"cold_start_ms": duration_ms}},
            )

    async def stop(self) -> None:
        log.engine.info("[browser] runtime.stop: entry")
        async with self._lock:
            await self._teardown_inside_lock()
        log.engine.info("[browser] runtime.stop: exit")

    async def _teardown_inside_lock(self) -> None:
        if self._attached:
            # WE DO NOT OWN THIS BROWSER. `close()` on a CDP-connected handle
            # disconnects and drops the contexts WE created; it does not stop the
            # remote browser, which was running before us and must survive us.
            # Killing an operator's real browser because an agent session ended
            # would be a far worse defect than a leaked connection.
            with contextlib.suppress(Exception):
                await self._browser.close()
            with contextlib.suppress(Exception):
                await self._pw.stop()
            if self._launched is not None:
                # WE started this one, so we stop it. Leaving it running would
                # leak a browser process on every restart of a long-lived device.
                from stackowl.tools.browser.cdp_launcher import (
                    cleanup_profile,
                    stop_cdp_browser,
                )

                with contextlib.suppress(Exception):
                    stop_cdp_browser(self._launched)
                with contextlib.suppress(Exception):
                    cleanup_profile(self._launched)
                self._launched = None
            self._browser = None
            self._pw = None
            self._attached = False
            self.available = False
            log.engine.info("[browser] runtime.attach: disconnected (remote browser left running)")
            return
        if self._manager is None:
            self._browser = None
            return
        with contextlib.suppress(Exception):
            await self._manager.__aexit__(None, None, None)
        self._browser = None
        self._manager = None
        self.available = False

    async def _recycle_inside_lock(self, reason: str) -> None:
        log.engine.warning(
            "[browser] runtime.recycle: restarting",
            extra={"_fields": {"reason": reason, "nav_count": self._nav_count}},
        )
        # COUNT THE ATTEMPT, NOT THE SUCCESS, and count it BEFORE opening.
        # _recycle_count used to increment only after a successful reopen, while
        # _effective_backend() is consulted DURING that reopen — so the escalation
        # check always read a number one behind and needed three deaths to react
        # to a threshold of two. "How many times did this engine have to be
        # restarted" is the question, and an attempt answers it.
        self._recycle_count += 1
        await self._teardown_inside_lock()
        try:
            # THE SAME OPENER start() USES. This branch used to hardcode the local
            # engine, so an ATTACHED runtime recycled by tearing down its CDP
            # connection and then launching the very engine it was configured to
            # avoid — measured 2026-08-26, first real turn after attach shipped:
            # "sessions.open: runtime recycled mid-open" and every browser_navigate
            # failing with BrowserRuntimeRecycledError. Two copies of one rule, and
            # only one of them learned about backends.
            self._browser = await self._open_backend()
            self._local_failures = 0
        except Exception as exc:
            # COUNT IT HERE TOO. Almost every real failure on this box arrives
            # through RECYCLE, not cold start — 43 "process gone" events against
            # one clean boot. Counting only in start() would leave the escalation
            # permanently at zero while the engine died all day, which is the
            # actuator-on-some-paths defect this codebase keeps finding.
            if self._effective_backend() == "local":
                self._local_failures += 1
            self._unavailable_reason = f"recycle failed: {type(exc).__name__}: {exc}"
            self.available = False
            log.engine.error(
                "[browser] runtime.recycle: restart FAILED",
                exc_info=exc,
                extra={"_fields": {"reason": self._unavailable_reason}},
            )
            return
        self.available = True
        self._nav_count = 0
        self._last_nav = time.monotonic()
        self._last_recycle_at = self._last_nav
        self._last_recycle_reason = reason
        self._attach_disconnect_listener()
        log.engine.info("[browser] runtime.recycle: restart ok")
        self._fire_on_recycled()

    async def ensure_available(self) -> None:
        """Ensure runtime has a live browser; recycle if dead. Self-healing entry point."""
        if self.available and self._browser is not None:
            return
        log.engine.info(
            "[browser] runtime.ensure_available: restarting dead runtime",
            extra={"_fields": {"reason": self._unavailable_reason}},
        )
        async with self._lock:
            if self.available and self._browser is not None:
                return  # raced — another caller already restarted
            if self._browser is None and self._manager is None:
                # Never started yet — do a fresh start path (no teardown needed).
                t0 = time.monotonic()
                try:
                    from camoufox.async_api import AsyncCamoufox

                    self._manager = AsyncCamoufox(**self._kwargs_from_settings())  # type: ignore[no-untyped-call]
                    self._browser = await self._manager.__aenter__()
                except Exception as exc:
                    self._unavailable_reason = f"{type(exc).__name__}: {exc}"
                    self.available = False
                    log.engine.error(
                        "[browser] runtime.ensure_available: start failed",
                        exc_info=exc,
                    )
                    raise RuntimeError(
                        f"browser runtime unavailable: {self._unavailable_reason}"
                    ) from exc
                self.available = True
                self._started_at = time.monotonic()
                self._nav_count = 0
                self._last_nav = self._started_at
                self._attach_disconnect_listener()
                log.engine.info(
                    "[browser] runtime.ensure_available: started",
                    extra={"_fields": {"cold_start_ms": (self._started_at - t0) * 1000.0}},
                )
                return
            await self._recycle_inside_lock(reason="ensure_available — runtime dead")
        if not self.available:
            raise RuntimeError(f"browser runtime unavailable: {self._unavailable_reason}")

    async def recycle_if_needed(self) -> None:
        """Check counters; trigger restart inside the lock if thresholds crossed."""
        threshold = self._settings.nav_recycle_threshold
        idle_secs = self._settings.idle_recycle_minutes * 60.0
        now = time.monotonic()
        if self._nav_count < threshold and (now - self._last_nav) < idle_secs:
            return
        async with self._lock:
            # Re-check inside lock to avoid double-restart.
            now2 = time.monotonic()
            if self._nav_count >= threshold:
                await self._recycle_inside_lock(reason=f"nav_count>={threshold}")
            elif (now2 - self._last_nav) >= idle_secs:
                await self._recycle_inside_lock(reason=f"idle>={self._settings.idle_recycle_minutes}min")

    def _profile_dir(self, owner_key: str, profile_name: str) -> Path:
        """Per-owner namespaced profile path."""
        # Sanitize owner_key for filesystem safety (replace ':' from telegram:chat_id).
        safe_owner = owner_key.replace(":", "_").replace("/", "_")
        safe_profile = profile_name.replace(":", "_").replace("/", "_")
        return self._settings.profiles_dir / safe_owner / safe_profile

    async def open_context(
        self,
        *,
        owner_key: str = "local",
        profile_name: str | None = None,
        proxy: ProxyConfig | None = None,
    ) -> Any:
        """Return a Playwright BrowserContext.

        - Incognito when ``profile_name`` is None (single Browser, new_context).
        - Persistent profile when ``profile_name`` is provided — uses Playwright's
          ``launch_persistent_context``-style mechanism via Camoufox's
          ``persistent_context=True`` parameter. NOTE: persistent contexts run
          in a separate Camoufox process; they do NOT share the runtime's
          shared Browser. This is by design — profiles isolate cookies/state.
        """
        await self.recycle_if_needed()
        await self.ensure_available()

        if profile_name is not None:
            return await self._open_persistent_context(owner_key, profile_name, proxy)

        ctx_kwargs: dict[str, Any] = {}
        if proxy is not None:
            ctx_kwargs["proxy"] = _proxy_to_dict(proxy)
        if self._settings.downloads_dir:
            self._settings.downloads_dir.mkdir(parents=True, exist_ok=True)
        try:
            ctx = await self._browser.new_context(**ctx_kwargs)
        except Exception as exc:
            from stackowl.infra.resilience import looks_like_dead_handle

            if not looks_like_dead_handle(exc):
                raise
            log.engine.warning(
                "[browser] runtime.open_context: dead handle — recycling and retrying once",
                exc_info=exc,
            )
            self._mark_disconnected()
            await self.ensure_available()
            ctx = await self._browser.new_context(**ctx_kwargs)
        log.engine.debug(
            "[browser] runtime.open_context: incognito",
            extra={"_fields": {"owner_key": owner_key}},
        )
        return ctx

    async def _open_persistent_context(
        self,
        owner_key: str,
        profile_name: str,
        proxy: ProxyConfig | None,
    ) -> Any:
        from camoufox.async_api import AsyncCamoufox

        profile_dir = self._profile_dir(owner_key, profile_name)
        profile_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            profile_dir.chmod(0o700)

        kwargs = self._kwargs_from_settings()
        kwargs["persistent_context"] = True
        kwargs["user_data_dir"] = str(profile_dir)
        if proxy is not None:
            kwargs["proxy"] = _proxy_to_dict(proxy)

        log.engine.info(
            "[browser] runtime.open_context: persistent",
            extra={"_fields": {"owner_key": owner_key, "profile_name": profile_name, "path": str(profile_dir)}},
        )
        manager: Any = AsyncCamoufox(**kwargs)  # type: ignore[no-untyped-call]
        ctx: Any = await manager.__aenter__()
        # Store the manager on the context so the registry can __aexit__ it on close.
        ctx._stackowl_persistent_manager = manager
        return ctx

    async def acquire_domain_slot(self, url: str) -> None:
        """Per-domain leaky-bucket rate-limit. Awaits if too soon."""
        host = urlparse(url).hostname or ""
        if not host:
            return
        lock = self._domain_locks[host]
        async with lock:
            last = self._domain_last_nav.get(host, 0.0)
            now = time.monotonic()
            wait = self._settings.per_domain_rate_limit_seconds - (now - last)
            if wait > 0:
                log.engine.debug(
                    "[browser] runtime.rate_limit: queued",
                    extra={"_fields": {"host": host, "wait_s": round(wait, 2)}},
                )
                await asyncio.sleep(wait)
            self._domain_last_nav[host] = time.monotonic()

    async def record_navigation(self) -> None:
        """Bookkeeping after every navigation. Triggers recycle when thresholds cross."""
        self._nav_count += 1
        self._last_nav = time.monotonic()
        await self.recycle_if_needed()

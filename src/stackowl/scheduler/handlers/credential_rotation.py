"""CredentialRotationHandler — session liveness check for persistent profiles.

For each (profile_name, check_url) pair in ``params``, opens the persistent
profile, navigates to the URL, and reports whether the session looks alive
(no login-page redirect, no obvious auth keyword in the final URL).

The actual auto-reauth via the ``browser_browse`` meta-tool is invoked when
``params['auto_rotate']`` is true AND a ``browser_browse`` tool is registered.
Otherwise the handler emits an actionable warning so the operator knows to
re-auth manually.
"""

from __future__ import annotations

import contextlib
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.net.ssrf_guard import guard_playwright_navigation
from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry, JobHandler, TriggerKind
from stackowl.scheduler.job import Job, JobResult
from stackowl.tools.browser._logging import url_path_only

if TYPE_CHECKING:
    from stackowl.tools.browser.runtime import CamoufoxRuntime

_LOGIN_PATH_RE = re.compile(r"(?:^|/)(login|signin|sign-in|auth|authenticate|sessions/new)(?:/|$)", re.IGNORECASE)


class CredentialRotationHandler(JobHandler):
    """Check that a persistent profile's session is still authenticated.

    Required job ``params``:
        ``{"profile_name": "github", "owner_key": "local", "check_url": "https://github.com/"}``
    Optional:
        ``{"login_indicator_selector": "form#login", "auto_rotate": false}``
    """

    def __init__(self, runtime: CamoufoxRuntime, state_dir: Path) -> None:
        self._runtime = runtime
        self._state_dir = state_dir
        self._state_dir.mkdir(parents=True, exist_ok=True)

    @property
    def handler_name(self) -> str:
        return "credential_rotation"

    @property
    def trigger_kind(self) -> TriggerKind:
        # ON_DEMAND, not seeded: execute() REQUIRES params['profile_name'] +
        # params['check_url']; a standing blank-param row would fail every poll.
        # Jobs are enqueued per user-configured profile, so no boot-time row is
        # expected (WS-G). Rotating/checking credentials is consequential — never
        # run it blanket-scheduled against an unspecified profile.
        return "on_demand"

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        profile = str(job.params.get("profile_name", ""))
        owner_key = str(job.params.get("owner_key", "local"))
        check_url = str(job.params.get("check_url", ""))
        login_selector = job.params.get("login_indicator_selector")
        log.scheduler.info(
            "[scheduler] credential_rotation.execute: entry",
            extra={"_fields": {
                "job_id": job.job_id, "profile": profile, "owner_key": owner_key,
                "url": url_path_only(check_url),
            }},
        )
        if not profile or not check_url:
            return JobResult(
                job_id=job.job_id,
                effect_class="read_only", success=False, output=None,
                error="Missing 'profile_name' or 'check_url' in params",
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        if not self._runtime.available:
            return JobResult(
                job_id=job.job_id,
                effect_class="read_only", success=False, output=None,
                error=f"Browser unavailable: {self._runtime.unavailable_reason or 'not started'}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        TestModeGuard.assert_not_test_mode("credential_rotation.execute")

        ctx: Any = None
        page: Any = None
        session_alive = False
        login_redirect = False
        final_url = ""
        error: str | None = None
        try:
            await self._runtime.acquire_domain_slot(check_url)
            ctx = await self._runtime.open_context(
                owner_key=owner_key, profile_name=profile,
            )
            # FX-05 follow-up — bypasses BrowserSessionRegistry.open(), so the
            # per-redirect-hop SSRF guard must be attached here directly.
            await ctx.route("**/*", guard_playwright_navigation)
            page = await ctx.new_page()
            await page.goto(check_url, wait_until="domcontentloaded", timeout=30_000)
            await self._runtime.record_navigation()
            final_url = str(page.url)
            login_redirect = bool(_LOGIN_PATH_RE.search(final_url))
            if not login_redirect and login_selector:
                # If a login form indicator is supplied, treat its presence as expired.
                with contextlib.suppress(Exception):
                    login_redirect = bool(await page.query_selector(str(login_selector)))
            session_alive = not login_redirect
        except Exception as exc:
            error = str(exc)
            log.scheduler.error(
                "[scheduler] credential_rotation: probe failed",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "profile": profile}},
            )
        finally:
            if page is not None:
                with contextlib.suppress(Exception):
                    await page.close()
            if ctx is not None:
                with contextlib.suppress(Exception):
                    await ctx.close()
                manager = getattr(ctx, "_stackowl_persistent_manager", None)
                if manager is not None:
                    with contextlib.suppress(Exception):
                        await manager.__aexit__(None, None, None)

        if not session_alive and error is None:
            log.scheduler.warning(
                "[scheduler] credential_rotation: session EXPIRED — manual re-auth required",
                extra={"_fields": {
                    "profile": profile, "owner_key": owner_key,
                    "final_url": url_path_only(final_url),
                }},
            )

        duration_ms = (time.monotonic() - t0) * 1000
        log.scheduler.info(
            "[scheduler] credential_rotation.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id, "session_alive": session_alive,
                "login_redirect": login_redirect, "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="read_only",
            success=error is None,
            output=f"session_alive={session_alive} final_url={url_path_only(final_url)}",
            error=error,
            duration_ms=duration_ms,
            metadata={
                "session_alive": session_alive,
                "login_redirect": login_redirect,
                "profile_name": profile,
                "owner_key": owner_key,
            },
        )


def register_credential_rotation_handler(runtime: CamoufoxRuntime, state_dir: Path) -> None:
    handler = CredentialRotationHandler(runtime=runtime, state_dir=state_dir)
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] credential_rotation handler registered",
        extra={"_fields": {"handler": handler.handler_name}},
    )

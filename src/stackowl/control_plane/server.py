"""The control-plane HTTP surface — one door, locked before it is built.

Design: ``docs/reference-mapping/designs/A05.1.md``.

WHY THIS IS NOT MOUNTED ON THE WEBHOOK RECEIVER'S APP, which is the obvious
economy and was measured before being rejected. Four couplings, of which the
first is decisive on its own:

1. It would ship OFF. ``WebhookSettings.enabled`` defaults ``False`` and the
   orchestrator gates the receiver's whole construction on it, so a control
   plane mounted there would exist only for operators who had already opted
   into webhooks — the built-but-not-wired shape this tree names as its
   commonest defect.
2. One app, two opposed response policies. The receiver returns a deliberately
   uniform 401 for unknown-source AND bad-signature so sources cannot be
   enumerated (F139); an app-level middleware runs over ``/webhook/{source}``
   too, so a control-plane rejection would be indistinguishable from a bad HMAC
   secret and the sender would re-provision a secret that was never the problem.
3. One bind, two security postures — and the receiver's own warning tells the
   operator to widen its bind.
4. One failure account: ``SupervisedTask`` parks a task permanently after
   repeated failures, so either surface's fault would take the other down.

They share a library and :mod:`stackowl.control_plane.auth`. Nothing else.
"""

from __future__ import annotations

import asyncio
import time as _time
from typing import TYPE_CHECKING, Any

from stackowl.control_plane.auth import (
    READ,
    UNAUTHORIZED_BODY,
    UNAUTHORIZED_STATUS,
    CredentialUnavailable,
    authenticate,
    check_origin,
    ensure_credential,
    is_loopback,
)
from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.config.settings import Settings
    from stackowl.health.aggregator import HealthAggregator
    from stackowl.scheduler.scheduler import JobScheduler

from stackowl.supervisor.supervisor import SupervisedTask


def _web() -> Any:
    """Lazy import so this module is importable without aiohttp installed."""
    from aiohttp import web as _w  # noqa: PLC0415

    return _w


class ControlPlaneServer(SupervisedTask):
    """Serves the control plane, or refuses to serve at all."""

    def __init__(
        self,
        settings: Settings,
        *,
        health: HealthAggregator | None = None,
        scheduler: JobScheduler | None = None,
    ) -> None:
        log.control_plane.info(
            "[control_plane] server.init: entry",
            extra={"_fields": {
                "has_health": health is not None,
                "has_scheduler": scheduler is not None,
            }},
        )
        self._settings = settings
        self._health = health
        #: The LIVE scheduler, injected — never constructed here. `list_jobs()`
        #: is the one reader the cron tool already uses, and a second reader of
        #: the same table is the defect this tree finds most often.
        self._scheduler = scheduler
        self._token: str = ""
        self._runner: Any = None
        self._site: Any = None
        self._stop_event: asyncio.Event | None = None

    @property
    def task_id(self) -> str:
        return "control_plane"

    # ------------------------------------------------------------------ run

    async def run(self) -> None:
        """Bind and serve. Refuses to bind when no credential can be had."""
        web = _web()
        cfg = self._settings.control_plane

        log.control_plane.info(
            "[control_plane] server.run: entry",
            extra={"_fields": {"bind": cfg.bind_address, "port": cfg.port}},
        )

        # INVARIANT I1 — no credential means no server. This is the branch that
        # `mcp/server.py::_sse_auth_ok` gets wrong by returning True, and the
        # difference between a gate and a warning is exactly here.
        try:
            self._token = ensure_credential()
        except CredentialUnavailable as exc:
            log.control_plane.error(
                "[control_plane] server.run: exit — refusing to bind without a "
                "credential; the control plane will NOT serve",
                exc_info=exc,
                extra={"_fields": {
                    "port": cfg.port,
                    "remedy": "fix the secret store (OS keyring, or write access "
                              "to ~/.stackowl/secrets) and restart",
                }},
            )
            raise

        app = web.Application()
        app.router.add_get("/api/v1/health", self._handle_health)
        app.router.add_get("/api/v1/schedules", self._handle_schedules)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, cfg.bind_address, cfg.port)
        try:
            await site.start()
        except Exception as exc:  # B5 — never silent
            log.control_plane.error(
                "[control_plane] server.run: site bind failed",
                exc_info=exc,
                extra={"_fields": {"bind": cfg.bind_address, "port": cfg.port}},
            )
            raise

        self._runner = runner
        self._site = site
        log.control_plane.info(
            "[control_plane] server.run: exit — listening",
            extra={"_fields": {"bind": cfg.bind_address, "port": cfg.port}},
        )
        self._warn_if_unreachable()

        # Block forever. Returning here after a successful bind makes the
        # supervisor treat the bind as "the task finished" and re-invoke run(),
        # whose second site then fails to bind against the first still-live
        # socket — five times, permanently parking the task while the original
        # listener kept serving. That is not hypothetical: it is the recorded
        # F145 follow-up on the webhook receiver, and this surface would
        # reproduce it exactly.
        self._stop_event = asyncio.Event()
        try:
            await self._stop_event.wait()
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Release the port. Safe to call when never started."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._site is not None:
            try:
                await self._site.stop()
            except Exception as exc:  # noqa: BLE001 — B5, never silent
                log.control_plane.warning(
                    "[control_plane] server.stop: site stop failed",
                    exc_info=exc,
                )
            self._site = None
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception as exc:  # noqa: BLE001 — B5, never silent
                log.control_plane.warning(
                    "[control_plane] server.stop: runner cleanup failed",
                    exc_info=exc,
                )
            self._runner = None
        log.control_plane.info("[control_plane] server.stop: exit — released")

    # ------------------------------------------------------- reachability

    def _warn_if_unreachable(self) -> None:
        """Say so when the bind cannot serve anyone but this machine.

        LISTENING IS NOT REACHABLE. The webhook receiver bound a port on 873
        boots and served zero requests because its configured senders could not
        reach loopback, and nothing said so until DEBT-302. A control plane
        exists to be opened by a customer, so the same silence would be worse
        here: the operator sees a started platform and an unreachable dashboard
        with no line connecting the two.

        It does NOT change the bind. That is a security-posture decision and it
        belongs to the operator (ESC-172); this makes the silence audible.
        """
        cfg = self._settings.control_plane
        if not is_loopback(cfg.bind_address):
            return
        log.control_plane.warning(
            "[control_plane] server.run: bound to loopback — reachable only "
            "from this machine",
            extra={"_fields": {
                "bind": cfg.bind_address,
                "port": cfg.port,
                "remedy": (
                    f"open http://localhost:{cfg.port} on this host, or set "
                    "control_plane.bind_address to an address your browser can "
                    "reach and restart"
                ),
            }},
        )

    # -------------------------------------------------------------- routes

    def _reject(self, web: Any) -> Any:
        """The uniform refusal. Invariant I5."""
        return web.Response(status=UNAUTHORIZED_STATUS, text=UNAUTHORIZED_BODY)

    def _guard(self, request: Any, route: str) -> tuple[Any | None, Any | None]:
        """The auth decision, ONCE. Returns ``(principal, None)`` or ``(None, response)``.

        WHY THIS EXISTS AT ALL, AND WHY NOW. Until A05.4 there was one route, so
        the origin check, the token check and the READ check sat inline in
        ``_handle_health`` and that was honest. The SECOND route is the moment
        that becomes two copies of one rule — this tree's third-commonest defect
        — and the moment a route can be written that forgets one of the three.

        The guard test was the sharper half of the problem: it read
        ``inspect.getsource(_handle_health)`` and asserted ``check_origin``
        appeared before ``authenticate``. That pins ONE HANDLER BY NAME, so a
        second route with no origin check at all would have passed it. The rule
        is now provable for every route at once — each handler calls this and
        performs no auth of its own.

        ORDER IS BEHAVIOUR, and it is preserved here rather than restated: a
        browser request from another origin may carry a valid credential, so
        checking the token first would authenticate an attack before rejecting
        it.
        """
        web = _web()
        cfg = self._settings.control_plane

        if not check_origin(
            request.headers.get("Origin"),
            request.headers.get("Host"),
            f"{cfg.bind_address}:{cfg.port}",
        ):
            log.control_plane.warning(
                f"[control_plane] server.{route}: exit — refused, cross-origin",
                extra={"_fields": {"origin": request.headers.get("Origin")}},
            )
            return None, self._reject(web)

        principal = authenticate(request.headers.get("Authorization"), self._token)
        if principal is None:
            log.control_plane.warning(
                f"[control_plane] server.{route}: exit — refused, unauthenticated"
            )
            return None, self._reject(web)

        if not principal.may(READ):
            log.control_plane.warning(
                f"[control_plane] server.{route}: exit — refused, principal may "
                "not read",
                extra={"_fields": {"principal_id": principal.principal_id}},
            )
            return None, web.Response(status=403, text=UNAUTHORIZED_BODY)

        return principal, None

    async def _handle_schedules(self, request: Any) -> Any:
        """`GET /api/v1/schedules` — every scheduled job, as a SET.

        THE GAP THIS CLOSES IS NARROWER THAN THE ITEM ORIGINALLY CLAIMED, and
        the narrowing is the point. A05.4 first said cron jobs were "invisible
        unless the operator queries the database by hand"; measured against the
        tree, ``tools/scheduling/cronjob.py`` declares EIGHT verbs — create,
        watch, list, update, pause, resume, remove, run. What a chat turn cannot
        do is show the whole set to somebody who is not at a chat prompt.

        So this READS THROUGH THE LIVE SCHEDULER — ``list_jobs()``, the same
        method the cron tool's own ``list`` calls — and issues no SQL of its
        own. A second reader of one table is how two answers to one question get
        born.

        ``jobs`` carries no ``owner_id`` column (asked of the live schema,
        2026-09-11), so there is no per-owner predicate to apply here and none
        is implied by omission.
        """
        web = _web()
        t0 = _time.monotonic()
        log.control_plane.info("[control_plane] server.schedules: entry")

        # `principal is None` is the narrowing check, not `refusal is not None`:
        # the first narrows the type for everything below, the second leaves an
        # `assert` to do it — and an assert in `src/` is a statement that can be
        # optimised away under -O.
        principal, refusal = self._guard(request, "schedules")
        if principal is None:
            return refusal

        if self._scheduler is None:
            log.control_plane.warning(
                "[control_plane] server.schedules: exit — no scheduler wired, "
                "so there is nothing to report",
                extra={"_fields": {"principal_id": principal.principal_id}},
            )
            return web.json_response({"schedules": [], "wired": False}, status=503)

        jobs = await self._scheduler.list_jobs()
        payload = {
            "wired": True,
            # failure_count and last_error are NOT decoration. A list that shows
            # a job exists but not that it has been failing is the A05.5 shape —
            # a surface that answers and leaves the reader worse off than silence
            # would have.
            "schedules": [
                {
                    "job_id": j.job_id,
                    "handler": j.handler_name,
                    "schedule": j.schedule,
                    # `enabled` IS a bool on the model — read directly. A
                    # `bool(...)` cast here would be defensive about a typed
                    # field, which is the habit DEBT-289 records the cost of.
                    "enabled": j.enabled,
                    "status": j.status,
                    "last_run_at": j.last_run_at,
                    "next_run_at": j.next_run_at,
                    "failure_count": j.failure_count,
                    "last_error": j.last_error,
                }
                for j in jobs
            ],
        }
        duration_ms = (_time.monotonic() - t0) * 1000
        log.control_plane.info(
            "[control_plane] server.schedules: exit — served",
            extra={"_fields": {
                "principal_id": principal.principal_id,
                "schedules": len(jobs),
                "duration_ms": duration_ms,
            }},
        )
        return web.json_response(payload)

    async def _handle_health(self, request: Any) -> Any:
        """`GET /api/v1/health` — the platform's own health, from the live core.

        Chosen as the first route because it is the only candidate that is a
        capability on the day it ships AND proves the auth decision: the payload
        names filesystem paths, provider names and spend anomalies, so
        200-with-body versus 401-without is a real difference. `/healthz` would
        have proved nothing, because it would have to be unauthenticated anyway.
        """
        web = _web()
        t0 = _time.monotonic()
        log.control_plane.info("[control_plane] server.health: entry")

        principal, refusal = self._guard(request, "health")
        if principal is None:
            return refusal

        if self._health is None:
            # The platform confessing a missing collaborator, at a level a
            # reader can see. DEBT-303 is the record of what happens when a
            # confession like this sits at DEBUG.
            log.control_plane.warning(
                "[control_plane] server.health: exit — no health aggregator "
                "wired, so there is nothing to report",
                extra={"_fields": {"principal_id": principal.principal_id}},
            )
            return web.json_response({"subsystems": [], "wired": False}, status=503)

        statuses = await self._health.collect()
        payload = {
            "wired": True,
            "subsystems": [
                {
                    "name": s.name,
                    "status": s.status,
                    "message": s.message,
                    # A REAL FIELD, read directly. `getattr(x, "remedy", None)`
                    # would be defensive about a field that exists, and DEBT-289
                    # is the record of what that costs: the whole reason the
                    # remedy channel sat unnoticed for 57 records is that every
                    # layer read it through a default and worked fine without it.
                    "remedy": s.remedy,
                    "latency_ms": s.latency_ms,
                }
                for s in statuses
            ],
        }
        duration_ms = (_time.monotonic() - t0) * 1000
        log.control_plane.info(
            "[control_plane] server.health: exit — served",
            extra={"_fields": {
                "principal_id": principal.principal_id,
                "subsystems": len(statuses),
                "duration_ms": duration_ms,
            }},
        )
        return web.json_response(payload)

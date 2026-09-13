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
import datetime as _dt
import hmac as _hmac
import time as _time
from typing import TYPE_CHECKING, Any, Literal

from stackowl.authz.bounds_guard import effective_bounds
from stackowl.commands.config_helpers import (
    collect_sensitive,
    config_path,
    flatten,
    load_yaml,
)
from stackowl.config.settings import Settings
from stackowl.control_plane.auth import (
    READ,
    UNAUTHORIZED_BODY,
    UNAUTHORIZED_STATUS,
    CredentialUnavailable,
    authenticate,
    check_origin,
    ensure_credential,
    is_loopback,
    read_credential,
)
from stackowl.control_plane.login_guard import (
    MAX_FAILURES,
    WINDOW_SECONDS,
    LoginAttempts,
)
from stackowl.control_plane.page import ICON_SVG, INDEX_HTML, MANIFEST_JSON
from stackowl.control_plane.password import (
    RESET_REMEDY,
    STORE_REMEDY,
    ControlPlanePassword,
    PasswordLookup,
    PasswordStoreUnavailable,
    SetupCode,
    weakness,
)
from stackowl.infra.observability import log
from stackowl.owls.activity import read_owl_activity
from stackowl.owls.skill_ownership import read_all_skill_ownership
from stackowl.owls.store import OwlStore
from stackowl.pipeline.durable.activity import read_task_activity
from stackowl.scheduler.run_history import read_run_history

if TYPE_CHECKING:
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool
    from stackowl.health.aggregator import HealthAggregator
    from stackowl.notifications.deliverer import ProactiveDeliverer
    from stackowl.scheduler.scheduler import JobScheduler

from stackowl.supervisor.supervisor import SupervisedTask

#: What a credential proof came to. `refused` is the brake, `wrong` the uniform 401.
_Proof = Literal["ok", "wrong", "refused"]

#: When a setup code issued at boot could not reach the owner's Telegram, how long
#: to wait before each further try. The channel adapters start beside this server,
#: not before it, so the first send can race them; after these the next sign-in
#: attempt in setup mode tries again.
_BOOT_DELIVERY_RETRY_S = (15.0, 60.0, 180.0)

#: How many lessons the memory browser shows at once. The corpus is 5,964 rows
#: and grows on every turn, so the page is a WINDOW onto it — the counts beside
#: it are what stop that window reading as the whole.
_MEMORY_PAGE = 50


#: How far back `recent_turns` looks on the agents route — the same three days
#: `owls_list` uses, so the two surfaces cannot report different activity for the
#: same owl. MEASURED 2026-09-11: eight of eleven owls have at least one outcome
#: inside it and the quietest have exactly one, so it separates quiet from silent.
_ACTIVITY_WINDOW_S = 3 * 24 * 60 * 60


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
        db: DbPool | None = None,
        deliverer: ProactiveDeliverer | None = None,
    ) -> None:
        log.control_plane.info(
            "[control_plane] server.init: entry",
            extra={"_fields": {
                "has_health": health is not None,
                "has_scheduler": scheduler is not None,
                "has_db": db is not None,
                "has_deliverer": deliverer is not None,
            }},
        )
        self._settings = settings
        self._health = health
        #: The LIVE scheduler, injected — never constructed here. `list_jobs()`
        #: is the one reader the cron tool already uses, and a second reader of
        #: the same table is the defect this tree finds most often.
        self._scheduler = scheduler
        #: The LIVE pool, injected — `read_all_skill_ownership` is the reader the
        #: gap itself names, and a second query over `skill_ownership` would be a
        #: second answer to one question.
        self._db = db
        self._token: str = ""
        #: Failed sign-ins per source. Bounded on purpose — its key is
        #: attacker-controlled, so an uncapped map is the denial of service it
        #: exists to prevent. Per-SERVER rather than global: a test builds its
        #: own instance and cannot be poisoned by another test's failures.
        self._login_attempts = LoginAttempts()
        #: THE ONE-TIME SETUP CODE'S WAY TO THE OWNER (Q29) — the same deliverer
        #: every proactive send uses, never a second path to Telegram.
        self._deliverer = deliverer
        #: THE ONE OWNER OF WHO OWNS THIS DASHBOARD (Q29). Stateless: every call
        #: reads the store, so a reset from the host CLI lands on the next request.
        self._password = ControlPlanePassword()
        #: Setup and password changes run one at a time, so two at once cannot
        #: interleave token rotation and hash writes (BH1) — exactly one wins.
        self._credential_lock = asyncio.Lock()
        #: At most one code issue/delivery at a time.
        self._code_lock = asyncio.Lock()
        #: scrypt is 16 MiB per derivation; unbounded parallel sign-ins would starve
        #: the executor and the memory with it (BH5).
        self._derivations = asyncio.Semaphore(2)
        #: Background code deliveries, held so they are not collected mid-flight.
        self._background: set[asyncio.Task[None]] = set()
        #: Set SYNCHRONOUSLY when a code task is spawned and cleared when it ends —
        #: checking the lock instead let a burst each spawn one before the first
        #: had taken it.
        self._code_pending = False
        #: `(issued_at, issuer)` of the code last offered to the terminal, so a boot
        #: retry does not print it again.
        self._terminal_offered: tuple[int, str] | None = None
        #: The gate state last logged loudly — see `_log_gate`.
        self._gate_state: str | None = None
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
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/manifest.webmanifest", self._handle_manifest)
        app.router.add_get("/icon.svg", self._handle_icon)
        app.router.add_get("/api/v1/health", self._handle_health)
        app.router.add_get("/api/v1/schedules", self._handle_schedules)
        app.router.add_get("/api/v1/config", self._handle_config)
        app.router.add_get("/api/v1/skills", self._handle_skills)
        app.router.add_get("/api/v1/tasks", self._handle_tasks)
        app.router.add_get("/api/v1/agents", self._handle_agents)
        app.router.add_post("/api/v1/login", self._handle_login)
        app.router.add_post("/api/v1/setup", self._handle_setup)
        app.router.add_post("/api/v1/password", self._handle_password)
        app.router.add_get("/api/v1/memory", self._handle_memory)
        app.router.add_get("/api/v1/interactions", self._handle_interactions)

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
        self._spawn_setup_code("start")

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

    # ------------------------------------------------------- the setup code

    def _spawn_setup_code(self, reason: str) -> None:
        """Make sure an install in setup mode has a setup code on its way to the owner.

        REPLACES THE BOOT WARNING ABOUT admin/admin (Q29). A warning named a
        published password; this issues the proof that replaces it. Called at
        start and whenever a request finds the install in setup mode — so a code
        that expired, or one the host CLI issued, reaches the owner on the next
        request without a restart.

        IN THE BACKGROUND, so no request waits on Telegram, and skipped while one
        is already running: a burst of refusals adds nothing.
        """
        if self._code_pending:
            return
        self._code_pending = True
        task = asyncio.create_task(self._ensure_setup_code(reason))
        self._background.add(task)
        task.add_done_callback(self._code_task_done)

    def _code_task_done(self, task: asyncio.Task[None]) -> None:
        self._background.discard(task)
        self._code_pending = False

    async def _settle_background(self) -> None:
        """Wait for background code work — for a caller that must observe its outcome."""
        while self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)

    async def _ensure_setup_code(self, reason: str) -> None:
        """Issue (or reuse) the setup code and deliver it once. Never raises."""
        t0 = _time.monotonic()
        # 1. ENTRY
        log.control_plane.info(
            "[control_plane] server.setup_code: entry",
            extra={"_fields": {"reason": reason}},
        )
        try:
            async with self._code_lock:
                # 2. DECISION — only a boot retries: the channels start beside this.
                delays = (0.0, *_BOOT_DELIVERY_RETRY_S) if reason == "start" else (0.0,)
                for delay in delays:
                    if delay:
                        await asyncio.sleep(delay)
                    # 3. STEP
                    if await self._issue_and_deliver(reason):
                        log.control_plane.info(
                            "[control_plane] server.setup_code: exit",
                            extra={"_fields": {
                                "reason": reason,
                                "duration_ms": (_time.monotonic() - t0) * 1000,
                            }},
                        )
                        return
        except Exception as exc:  # noqa: BLE001 — a background task dies silently otherwise
            log.control_plane.error(
                "[control_plane] server.setup_code: exit — issuing or delivering the "
                "setup code failed",
                exc_info=exc,
                extra={"_fields": {"reason": reason, "remedy": RESET_REMEDY}},
            )
            return
        # 4. EXIT — nothing reached the owner, loudly.
        log.control_plane.warning(
            "[control_plane] server.setup_code: exit — the setup code has not reached "
            "the owner's Telegram yet; the next sign-in attempt in setup mode tries again",
            extra={"_fields": {"reason": reason, "remedy": RESET_REMEDY}},
        )

    async def _issue_and_deliver(self, reason: str) -> bool:
        """One attempt. True when there is nothing left to do.

        THE STORE IS TOUCHED UNDER THE SAME LOCK AS SETUP, and delivery runs
        outside it. Marking a code sent reads it and writes it back; a setup
        that used the code up in between would have the used code written back.
        Holding the lock across the Telegram send instead would make a slow
        channel hold up the owner's setup.
        """
        try:
            async with self._credential_lock:
                state = await asyncio.to_thread(self._password.lookup)
                if state.state != "setup":
                    log.control_plane.info(
                        "[control_plane] server.setup_code: not in setup mode — no code needed",
                        extra={"_fields": {"reason": reason, "state": state.state}},
                    )
                    return True
                issued = await asyncio.to_thread(self._password.ensure_code)
        except PasswordStoreUnavailable as exc:
            log.control_plane.error(
                "[control_plane] server.setup_code: could not issue a setup code — the "
                "password store cannot be read",
                exc_info=exc,
                extra={"_fields": {"reason": reason, "remedy": exc.remedy}},
            )
            return True

        if issued.sent:
            # AN UNEXPIRED CODE IS NOT SENT AGAIN. A restart must not spam the owner.
            notice = log.control_plane.warning if reason == "start" else log.control_plane.info
            notice(
                "[control_plane] server.setup_code: the dashboard is in setup mode; the "
                "setup code sent earlier is still valid and is not sent again",
                extra={"_fields": {"expires": issued.expires_utc, "remedy": RESET_REMEDY}},
            )
            return True

        delivered, retry = await self._deliver_setup_code(issued)
        # MARKED SENT ONLY WHEN IT WAS. Marking a code that reached nobody makes
        # every later boot say "sent earlier, not sent again" for a day while the
        # owner has never seen it. MEASURED on the live box 2026-09-12: a core
        # that started before the orchestrator passed the deliverer logged "no
        # deliverer is wired", marked its code sent, and the next two boots
        # declined to send it.
        if delivered and not retry:
            try:
                async with self._credential_lock:
                    await asyncio.to_thread(self._password.mark_code_sent, issued)
            except PasswordStoreUnavailable as exc:
                log.control_plane.warning(
                    "[control_plane] server.setup_code: delivered, but could not record "
                    "it — a restart may send the code again",
                    exc_info=exc,
                )
        log.control_plane.warning(
            "[control_plane] server.setup_code: the dashboard is in setup mode — no "
            "data is served until the owner sets a password with the setup code",
            extra={"_fields": {
                "issuer": issued.issuer,
                "expires": issued.expires_utc,
                "delivered": delivered,
                "retrying": retry,
                "remedy": "the setup code goes to the owner's Telegram when exactly one "
                          "owner is configured and to the platform's terminal when it "
                          "has one; " + RESET_REMEDY,
            }},
        )
        return not retry

    async def _deliver_setup_code(self, issued: SetupCode) -> tuple[bool, bool]:
        """Send the code to the owner's Telegram and (platform-issued) the terminal.

        Returns ``(delivered, retry)``: whether ANY channel took it, and whether
        the owner's Telegram — an address resolves — still has not, so the send is
        worth trying again. A failure is logged and never blocks setup: the host
        CLI prints a code of its own.
        """
        if self._deliverer is None:
            log.control_plane.warning(
                "[control_plane] server.setup_code: no deliverer is wired, so the setup "
                "code reaches nobody from here",
                extra={"_fields": {"remedy": RESET_REMEDY}},
            )
            return False, False

        from stackowl.notifications.deliverer import SETUP_CODE_CATEGORY
        from stackowl.notifications.recipient import resolve_owner_addresses
        from stackowl.notifications.router import Notification

        message_with_code = (
            f"StackOwl dashboard setup code: {issued.display}\n"
            f"Single use, valid until {issued.expires_utc}. Open the dashboard on port "
            f"{self._settings.control_plane.port}, enter the code and choose a password "
            "of at least 12 characters."
        )
        owner = resolve_owner_addresses(self._settings, ["telegram"]).get("telegram")
        targets: list[tuple[str, str | int | None]] = []
        if owner is not None:
            targets.append(("telegram", owner))
        else:
            log.control_plane.warning(
                "[control_plane] server.setup_code: no single owner Telegram address "
                "resolves, so the setup code is not sent there",
                extra={"_fields": {
                    "remedy": "set exactly one telegram_channel.allowed_user_ids, or "
                              + RESET_REMEDY,
                }},
            )
        # THE TERMINAL gets the code the PLATFORM issued — ONCE per code in this
        # process, not on every boot retry; one the host CLI issued was already
        # printed where the operator typed the command.
        offer = (issued.issued_at, issued.issuer)
        if issued.issuer == "platform" and self._terminal_offered != offer:
            self._terminal_offered = offer
            targets.append(("cli", None))

        delivered = False
        telegram_reached = owner is None
        for channel, target in targets:
            try:
                status = await self._deliverer.deliver(
                    Notification(
                        message=message_with_code,
                        urgency="critical",
                        # A category the deliverer never writes into conversation
                        # history — the code is an ownership proof, not a message.
                        category=SETUP_CODE_CATEGORY,
                        channel_name=channel,
                        target=target,
                    ),
                    # A secret has no place in the undelivered-outbox banner.
                    surface_undelivered=False,
                )
            except Exception as exc:  # noqa: BLE001 — B5, never silent; never blocks setup
                status = "failed"
                log.control_plane.error(
                    "[control_plane] server.setup_code: delivery raised",
                    exc_info=exc,
                    extra={"_fields": {"channel": channel}},
                )
            log.control_plane.info(
                "[control_plane] server.setup_code: delivery attempted",
                extra={"_fields": {"channel": channel, "status": status}},
            )
            if status == "delivered":
                delivered = True
                if channel == "telegram":
                    telegram_reached = True
        return delivered, not telegram_reached

    async def stop(self) -> None:
        """Release the port. Safe to call when never started."""
        for task in list(self._background):
            task.cancel()
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

        ESC-172 IS ANSWERED, so this no longer fires on a default install. The
        operator's decision, 2026-09-12: *"Make it always available in all
        interfaces by default in platform. So when customer runs it it will work
        out of box."* `bind_address` therefore defaults to `0.0.0.0` and a
        customer who clones and runs reaches the dashboard from their own
        browser. This warning stays for the operator who NARROWS the bind back
        to loopback and then wonders why the page will not open — the silence it
        was built to break is still possible, it is just no longer the default.
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

    @staticmethod
    def _request_source(request: Any) -> str:
        """Who is knocking, for the login counter only.

        `request.remote` — the peer the socket is actually connected to — and
        deliberately NOT `X-Forwarded-For`. That header is set by the client
        unless a trusted proxy overwrites it, so honouring it here would let an
        attacker reset their own counter by inventing a new value per request,
        which is worse than having no counter at all. If this platform ever
        sits behind a real reverse proxy, the trusted-proxy list is the thing to
        add; guessing is not.
        """
        return str(getattr(request, "remote", None) or "unknown")

    def _origin_ok(self, request: Any, route: str) -> Any | None:
        """The ORIGIN half of the auth decision, in ONE place.

        Extracted when the login route arrived. `_guard` is the whole decision
        for a data route — origin, token, READ — but login cannot take the token
        half, because handing the token out is what login is FOR. It still must
        not be callable cross-origin: without that, any page on the internet
        could POST to it from a browser that can reach loopback and read the
        token out of the reply.

        So the check is shared rather than copied. `test_no_route_handler_
        performs_ITS_OWN_auth` forbids `check_origin(` inside a handler for a
        good reason — a route carrying two of the three checks looks finished —
        and that rule is satisfied here rather than exempted: there is exactly
        one copy of the origin rule and both callers ask it.

        Returns a refusal response, or ``None`` when the origin is acceptable.
        """
        # THE REQUEST CARRIES BOTH HALVES. This used to build an `expected_host`
        # from `bind_address:port`, which is the same string a browser sends ONLY
        # on loopback. With the bind now defaulting to every interface, a browser
        # at `http://192.168.1.50:8787` sends that as its Host and would have been
        # refused against `0.0.0.0:8787` — every request, while every loopback
        # test stayed green. See `check_origin`.
        if check_origin(
            request.headers.get("Origin"),
            request.headers.get("Host"),
        ):
            return None
        log.control_plane.warning(
            f"[control_plane] server.{route}: exit — refused, cross-origin",
            extra={"_fields": {"origin": request.headers.get("Origin")}},
        )
        return self._reject(_web())

    async def _guard(self, request: Any, route: str) -> tuple[Any | None, Any | None]:
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

        refusal = self._origin_ok(request, route)
        if refusal is not None:
            return None, refusal

        # THE STORED TOKEN, read per request. A `reset-password` on the host rotates
        # it from another process, and the in-memory copy would keep accepting the
        # old token and refuse the new one until a restart.
        stored_token = await asyncio.to_thread(read_credential)
        principal = authenticate(
            request.headers.get("Authorization"), stored_token or self._token
        )
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

        # THE OWNERSHIP GATE (Q29), AFTER the token on purpose: an unauthenticated
        # caller keeps the uniform 401. Re-read on every request, never cached, so
        # a reset from the host CLI takes effect on the next one.
        state = await asyncio.to_thread(self._password.lookup)
        if state.state == "unavailable":
            return None, self._store_unavailable(route, state.remedy)
        if state.state == "setup":
            self._spawn_setup_code(route)
            self._log_gate(
                "setup",
                f"[control_plane] server.{route}: exit — refused, the dashboard has no "
                "password yet (setup mode)",
                {
                    "principal_id": principal.principal_id,
                    "remedy": "set a password on the dashboard with the setup code; "
                              + RESET_REMEDY,
                },
            )
            return None, web.json_response({"error": "setup_required"}, status=403)

        self._gate_state = "ready"
        return principal, None

    def _log_gate(self, state: str, message: str, details: dict[str, Any]) -> None:
        """Loud the first time the dashboard is seen in *state*, quiet while it stays.

        Every panel is its own request, so an unreadable store or an install in
        setup mode wrote a WARNING per panel per page load — eight a refresh,
        burying the first and only useful one.
        """
        first = self._gate_state != state
        self._gate_state = state
        (log.control_plane.warning if first else log.control_plane.debug)(
            message, extra={"_fields": {**details, "repeat": not first}}
        )

    def _store_unavailable(self, route: str, remedy: str | None) -> Any:
        """503 with a remedy — an unreadable store is never read as "no password" (L3)."""
        self._log_gate(
            "unavailable",
            f"[control_plane] server.{route}: exit — refused, the password store cannot "
            "be read",
            {"remedy": remedy or STORE_REMEDY},
        )
        return _web().json_response(
            {"error": "password_store_unavailable", "remedy": remedy or STORE_REMEDY},
            status=503,
        )

    def _too_many(self, route: str, source: str) -> Any:
        """429 — the brake. Refuses rather than sleeping: a waiting handler holds a worker."""
        log.control_plane.warning(
            f"[control_plane] server.{route}: exit — refused, too many failed attempts "
            "from this source",
            extra={"_fields": {
                "source": source,
                "window_seconds": WINDOW_SECONDS,
                "max_failures": MAX_FAILURES,
            }},
        )
        return _web().json_response(
            {"error": "too many failed attempts — wait and try again"}, status=429,
        )

    async def _credential_fields(
        self, request: Any, route: str, names: tuple[str, ...],
    ) -> tuple[dict[str, str] | None, Any | None]:
        """The body of a credential route: a JSON object whose named fields are strings.

        A direct type check, never `str(...)`: a crafted client sending a number or a
        list must not have its Python repr stored as a password (EC10), and an array
        body is a 400, not a 500 (BH7).
        """
        web = _web()
        try:
            body = await request.json()
        except Exception as exc:  # noqa: BLE001 — B5, never silent
            log.control_plane.warning(
                f"[control_plane] server.{route}: exit — refused, unreadable body",
                exc_info=exc,
            )
            return None, web.json_response({"error": "expected a JSON body"}, status=400)
        if not isinstance(body, dict):
            log.control_plane.warning(
                f"[control_plane] server.{route}: exit — refused, the body is not a JSON "
                "object",
                extra={"_fields": {"body_type": type(body).__name__}},
            )
            return None, web.json_response({"error": "expected a JSON object"}, status=400)
        fields: dict[str, str] = {}
        for name in names:
            value = body.get(name)
            if value is None:
                value = ""
            if not isinstance(value, str):
                log.control_plane.warning(
                    f"[control_plane] server.{route}: exit — refused, a field is not a "
                    "string",
                    extra={"_fields": {"field": name}},
                )
                return None, web.json_response(
                    {"error": f"{name} must be a string"}, status=400
                )
            fields[name] = value
        return fields, None

    async def _prove_owner(
        self, source: str, username: str, password: str, state: PasswordLookup,
    ) -> _Proof:
        """THE ONE CREDENTIAL PROOF — login and the password change both ask it (BH7).

        COUNTED BEFORE THE HASH (BH5). The brake check and the count are one
        synchronous step with no `await` between them, so parallel guesses from one
        source cannot all pass the brake while scrypt runs; a success clears the
        count. BOTH HALVES ALWAYS RUN — the username in constant time, then the
        scrypt match — and are combined afterwards, so the answer never says which
        was wrong.
        """
        if self._login_attempts.is_refused(source):
            return "refused"
        self._login_attempts.record_failure(source)
        # BYTES, not str: `compare_digest` raises TypeError on a non-ASCII str,
        # which would make an accented username a 500 instead of a refusal.
        user_ok = _hmac.compare_digest(
            username.encode("utf-8"),
            self._settings.control_plane.username.encode("utf-8"),
        )
        async with self._derivations:
            pass_ok = await asyncio.to_thread(state.matches, password)
        if not (user_ok and pass_ok):
            return "wrong"
        self._login_attempts.record_success(source)
        return "ok"

    async def _prove_setup_code(
        self, source: str, presented: str, state: PasswordLookup,
    ) -> _Proof:
        """Whether *presented* is the current setup code — counted like a sign-in.

        Only an install in SETUP has a code to use. A ready one refuses even a code
        that is still stored, so a reset racing a password change in another
        process can never reopen the first-claim race. Raises
        :class:`PasswordStoreUnavailable`.
        """
        if self._login_attempts.is_refused(source):
            return "refused"
        if state.state != "setup":
            self._login_attempts.record_failure(source)
            return "wrong"
        # READ FIRST, COUNT AFTER. A store that cannot be read raises here and is
        # NOT a guess, so it must not brake the owner. The re-check and the count
        # below are one synchronous step and the compare needs no await, so
        # parallel guesses still cannot outrun the brake.
        stored = await asyncio.to_thread(self._password.current_code)
        if self._login_attempts.is_refused(source):
            return "refused"
        self._login_attempts.record_failure(source)
        if stored is None or not stored.matches(presented):
            return "wrong"
        self._login_attempts.record_success(source)
        return "ok"

    async def _replace_password(self, route: str, new_password: str, *, consume_code: bool) -> Any:
        """Rotate the token and store the hash as one step; 500 and nothing changed on failure."""
        web = _web()
        try:
            async with self._derivations:
                minted = await asyncio.to_thread(
                    self._password.replace_password, new_password, consume_code=consume_code,
                )
        except PasswordStoreUnavailable as exc:
            log.control_plane.error(
                f"[control_plane] server.{route}: exit — the password could not be "
                "stored; password, setup code and token are as they were",
                exc_info=exc,
                extra={"_fields": {"remedy": exc.remedy}},
            )
            return web.json_response(
                {"error": "password_store_unavailable", "remedy": exc.remedy}, status=500,
            )
        self._token = minted
        return web.json_response({"token": minted})

    async def _handle_manifest(self, request: Any) -> Any:
        """`GET /manifest.webmanifest` — what makes this installable.

        A browser REFUSES a `data:` manifest, so the "no external request"
        constraint is met by serving it from this door rather than by inlining
        it. It is not an `/api/` path, so the route/page bijection — which
        filters on that prefix — is untouched.

        NO INSTANCE STATE, DELIBERATELY, and that is what earns the unguarded
        exemption: a browser fetches a manifest before anyone has signed in, so
        it cannot present a credential. `start_url` is RELATIVE for the same
        reason — putting the configured port in here would need
        `self._settings` and forfeit the structural carve-out.
        """
        return _web().Response(
            text=MANIFEST_JSON, content_type="application/manifest+json"
        )

    async def _handle_icon(self, request: Any) -> Any:
        """`GET /icon.svg` — the mark, for a tab and a home screen.

        Unguarded for the same structural reason as the manifest, and it
        discloses nothing: a shape and two colours. Reads no instance state.
        """
        return _web().Response(text=ICON_SVG, content_type="image/svg+xml")

    async def _handle_index(self, request: Any) -> Any:
        """`GET /` — the page. THE ONE ROUTE WITH NO CREDENTIAL CHECK.

        A browser cannot put an `Authorization` header on a top-level
        navigation, so a page served only to an authenticated caller could never
        be opened — the dashboard would exist and be unreachable, which is the
        state A05.1 actually shipped while its record said the surface was done.

        IT IS SAFE FOR ONE REASON, AND THE REASON IS ENFORCED RATHER THAN
        PROMISED: the response is a module-level CONSTANT with no interpolation,
        so it cannot carry platform state even by accident. This handler reads
        no settings, no health, no scheduler — a tripwire walks its body and
        fails if it ever does. Every byte of data the rendered page shows
        arrives from `fetch()` calls the browser makes afterwards, and each of
        those goes through `_guard` like everything else.
        """
        web = _web()
        log.control_plane.info("[control_plane] server.index: exit — page served")
        return web.Response(text=INDEX_HTML, content_type="text/html")

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
        principal, refusal = await self._guard(request, "schedules")
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

        # WHAT THEY ACTUALLY DID, from the table that had 22,647 rows and no
        # reader. `list_jobs()` answers what is DEFINED; `job_runs` answers what
        # HAPPENED, and until 2026-09-12 nothing joined the two — so 170 jobs
        # rendered identically and the 131 that had not run in a day looked
        # exactly like the 38 that had.
        #
        # THE JOIN IS HERE, IN PYTHON, ON PURPOSE. `jobs` has exactly one reader
        # (`list_jobs`) and `job_runs` now has exactly one (`read_run_history`);
        # a SQL join would have given one of them a second, which this route's
        # own docstring calls "how two answers to one question get born".
        history: dict[str, Any] = {}
        runs_gaps = None
        if self._db is not None:
            history, runs_gaps = await read_run_history(self._db)

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
                    # NAMED WITHOUT THE WINDOW, because `window_hours` is emitted
                    # once beside them. `runs_24h` would put the same fact in two
                    # places and make changing the window a rename across three
                    # surfaces — the two-copies-of-one-rule shape this tree finds
                    # more often than any other.
                    #
                    # `None` is a REAL answer and is not the same as zero: it
                    # means this read could not see the history at all (no db
                    # wired), while `0` means the job did not run inside the
                    # window. A surface that renders both as "0 runs" reports an
                    # unwired reader as an idle scheduler.
                    "runs": (
                        history[j.job_id].runs if j.job_id in history
                        else (0 if runs_gaps is not None else None)
                    ),
                    "failures": (
                        history[j.job_id].failures if j.job_id in history
                        else (0 if runs_gaps is not None else None)
                    ),
                    "typical_ms": (
                        history[j.job_id].typical_ms if j.job_id in history else None
                    ),
                    "last_ran_at": (
                        history[j.job_id].last_ran_at if j.job_id in history else None
                    ),
                }
                for j in jobs
            ],
        }
        if runs_gaps is not None:
            silent = sum(
                1 for j in jobs if j.enabled and j.job_id not in history
            )
            payload["window_hours"] = runs_gaps.window_hours
            payload["retention_days"] = runs_gaps.retention_days
            # THE HORIZON, so a zero is never ambiguous. `db_reclaim` prunes at
            # `retention_days`, so "no runs" means the job was idle only while
            # the window sits inside this timestamp; past it, it means nobody can
            # tell any more.
            payload["horizon_at"] = runs_gaps.horizon_at
            payload["runs"] = runs_gaps.total_runs
            payload["failures"] = runs_gaps.total_failures
            payload["silent_enabled_jobs"] = silent
        duration_ms = (_time.monotonic() - t0) * 1000
        log.control_plane.info(
            "[control_plane] server.schedules: exit — served",
            extra={"_fields": {
                "principal_id": principal.principal_id,
                "schedules": len(jobs),
                "runs": runs_gaps.total_runs if runs_gaps else None,
                "silent_enabled_jobs": (
                    sum(1 for j in jobs if j.enabled and j.job_id not in history)
                    if runs_gaps else None
                ),
                "duration_ms": duration_ms,
            }},
        )
        return web.json_response(payload)

    async def _handle_skills(self, request: Any) -> Any:
        """`GET /api/v1/skills` — which owl owns which skill.

        THE GAP NAMED ITS OWN READER, so this uses it.
        `owls/skill_ownership.py::read_all_skill_ownership` already returns
        `owl_name -> [skill names]`, owner-scoped. A05.8's gap says the state is
        stored and has a reader and that "no command or surface presents it" —
        so the work is presentation, and a second query over `skill_ownership`
        would be a second answer to one question.

        MEASURED 2026-09-11 before building: **35 ownership rows across SEVEN
        owls** (verifier 12, secretary 9, scout 4, rca_gatherer 4, jobmarket 3,
        mailbutler 2, hypothesis 1) against 46 skills — and
        `commands/skill_command.py` mentions ownership NOWHERE. Its only
        `owner` matches are GitHub URL parsing (`owner/repo`).

        SCOPED TO OWNERSHIP, NOT THE CATALOGUE. Listing every skill's metadata
        would need the skill STORE injected as well, and the store has no
        list-all — `skills_list.py` unions `list_for_source` across sources and
        says so in its own docstring. That is a second item; this closes the
        half the gap actually measured.
        """
        web = _web()
        t0 = _time.monotonic()
        log.control_plane.info("[control_plane] server.skills: entry")

        principal, refusal = await self._guard(request, "skills")
        if principal is None:
            return refusal

        if self._db is None:
            log.control_plane.warning(
                "[control_plane] server.skills: exit — no db wired, so there is "
                "nothing to report",
                extra={"_fields": {"principal_id": principal.principal_id}},
            )
            return web.json_response({"owls": [], "wired": False}, status=503)

        owned = await read_all_skill_ownership(self._db)
        payload = {
            "wired": True,
            "owls": [
                {"owl": owl, "skills": sorted(skills), "count": len(skills)}
                for owl, skills in sorted(owned.items())
            ],
        }
        duration_ms = (_time.monotonic() - t0) * 1000
        log.control_plane.info(
            "[control_plane] server.skills: exit — served",
            extra={"_fields": {
                "principal_id": principal.principal_id,
                "owls": len(owned),
                "owned_skills": sum(len(v) for v in owned.values()),
                "duration_ms": duration_ms,
            }},
        )
        return web.json_response(payload)

    async def _handle_memory(self, request: Any) -> Any:
        """`GET /api/v1/memory` — what the platform has actually learned.

        **A05.5's GAP NAMED THE WRONG STORE, and that is the whole finding.** It
        said "the 237 facts in `staged_facts` are reachable from no surface" and
        that `committed_facts` "holds ZERO rows". Both are true and neither is
        the answer:

        * `committed_facts` is RETIRED, not broken — 0 rows since migration 0112,
          and `pipeline/state.py` records that its promotion path is "dead on
          both ends". A browser over it would show an empty retired table.
        * `staged_facts` is SHORT-TERM CONVERSATION HISTORY. The same comment
          calls those rows "ONLY short-term history … the mirror that lets the
          agent know what it already told him". Rendering 237 of them under the
          heading "what the platform remembers about you" would MISREPRESENT a
          transcript as knowledge.
        * `lessons` holds **5,964 rows** — 5,689 reflections, 218 skill, 57
          tool-heuristic — written continuously, newest twenty minutes before
          this route existed, and reachable from NO command and NO route.

        So the memory an operator is owed is the lessons corpus, and the other
        stores appear as COUNTS with their nature stated rather than as rows
        pretending to be memory. A surface that shows the wrong store confidently
        is worse than one that shows nothing.

        IT EXTENDS THE EXISTING STORE. `LessonsStore` already had `search` (which
        needs a `query_embedding`) and `count`, and nothing between them —
        `recent()` is the browse primitive added beside them, because an operator
        asking "what do you remember" has no query to embed and should not have
        to guess one.
        """
        web = _web()
        t0 = _time.monotonic()
        log.control_plane.info("[control_plane] server.memory: entry")

        principal, refusal = await self._guard(request, "memory")
        if principal is None:
            return refusal

        if self._db is None:
            log.control_plane.warning(
                "[control_plane] server.memory: exit — no db wired, so there is "
                "nothing to report",
                extra={"_fields": {"principal_id": principal.principal_id}},
            )
            return web.json_response({"lessons": [], "wired": False}, status=503)

        from stackowl.learning.lessons_store import SqliteLessonsStore

        store = SqliteLessonsStore(self._db)
        lessons = await store.recent(limit=_MEMORY_PAGE)
        by_source = await store.counts_by_source()
        from stackowl.memory.activity import (
            read_curated_entries,
            read_other_memory_counts,
        )

        other = await read_other_memory_counts(
            self._db, owner_id=principal.principal_id
        )
        curated = read_curated_entries()

        payload = {
            "wired": True,
            # CURATED FIRST, because it is what the operator means. `/memory
            # search` already reads these and nothing shows them as a SET —
            # MEASURED: 19 files, 64 entries, of which USER.md's 7 are about the
            # person and the rest are per-owl notes. This is the smallest and the
            # most load-bearing of the five stores.
            #
            # EVERY KEY BELOW IS WRITTEN HERE, not returned ready-made by a
            # helper. The field-bijection guard walks the `_handle_*` methods for
            # the keys a route emits, so a payload assembled in a store module is
            # invisible to it — eight fields were, measured off-tree before this
            # landed. Every other route on this dashboard declares its shape in
            # the handler and this keeps that true.
            "curated": [
                {
                    "target": t.target,
                    "entries": [
                        {"text": e.text, "durability": e.durability}
                        for e in t.entries
                    ],
                }
                for t in curated
            ],
            "lessons": [
                {
                    "lesson_id": r.lesson_id,
                    "source_type": r.source_type,
                    "source_ref": r.source_ref,
                    "content": r.content,
                    "created_at": r.created_at,
                }
                for r in lessons
            ],
            "counts_by_source": by_source,
            "total": sum(by_source.values()),
            # NAMED, NOT MERGED. Each of these is a different KIND of remembering
            # and the page says which is which; adding them into one "memories"
            # number is how a transcript gets counted as knowledge.
            "other_stores": [
                {"store": o.store, "kind": o.kind, "rows": o.rows, "note": o.note}
                for o in other
            ],
        }

        duration_ms = (_time.monotonic() - t0) * 1000
        log.control_plane.info(
            "[control_plane] server.memory: exit — served",
            extra={"_fields": {
                "principal_id": principal.principal_id,
                "curated_entries": sum(len(t.entries) for t in curated),
                "shown": len(lessons),
                "total_lessons": sum(by_source.values()),
                "duration_ms": duration_ms,
            }},
        )
        return web.json_response(payload)

    async def _handle_interactions(self, request: Any) -> Any:
        """`GET /api/v1/interactions` — the edges between agents, as a set.

        **A05.7's gap named the wrong mechanism.** It said delegation, parliament
        and owl-to-owl messages "happen inside mailboxes nobody can watch". The
        a2a mailbox IS an in-memory `asyncio.Queue` whose 14 log calls are DEBUG
        against zero DEBUG records — so that clause is true and it is about the
        message HOP, not the edge. The EDGE is durably recorded in two stores,
        and the actual gap is that no command and no route has ever read either:
        `tasks.parent_task_id` (52 rows, all same-owl decomposition, current) and
        `side_effect_ledger` `tool_name='delegate_task'` (17 rows carrying
        `to_owl` and an outcome).

        IT REPORTS BOTH KINDS SEPARATELY AND NEVER SUMS THEM. 52 of the 52
        `tasks` edges are an owl decomposing its OWN work; presenting that beside
        cross-owl delegation as one "interactions" count would report a busy
        multi-agent platform that is one owl talking to itself.
        """
        web = _web()
        t0 = _time.monotonic()
        log.control_plane.info("[control_plane] server.interactions: entry")

        principal, refusal = await self._guard(request, "interactions")
        if principal is None:
            return refusal

        if self._db is None:
            log.control_plane.warning(
                "[control_plane] server.interactions: exit — no db wired, so "
                "there is nothing to report",
                extra={"_fields": {"principal_id": principal.principal_id}},
            )
            return web.json_response({"edges": [], "wired": False}, status=503)

        from stackowl.pipeline.durable.interactions import read_agent_interactions

        edges, gaps = await read_agent_interactions(
            self._db, owner_id=principal.principal_id
        )

        payload = {
            "wired": True,
            # EVERY KEY WRITTEN HERE, never returned ready-made by the reader:
            # the field-bijection guard walks `_handle_*` for the keys a route
            # emits, and A05.5 measured eight fields going invisible when a
            # helper assembled the payload instead.
            "edges": [
                {
                    "kind": e.kind,
                    "from_owl": e.from_owl,
                    "to_owl": e.to_owl,
                    "outcome": e.outcome,
                    "at": e.at,
                    "ref": e.ref,
                    "detail": e.detail,
                }
                for e in edges
            ],
            "gaps": {
                "unseen_other_owner": gaps.unseen_other_owner,
                "truncated": gaps.truncated,
                "delegations_without_a_target": gaps.delegations_without_a_target,
                "delegations_without_a_caller": gaps.delegations_without_a_caller,
                "newest_delegation_recorded": gaps.newest_delegation_recorded,
                "parliament_sessions": gaps.parliament_sessions,
            },
        }

        duration_ms = (_time.monotonic() - t0) * 1000
        log.control_plane.info(
            "[control_plane] server.interactions: exit — served",
            extra={"_fields": {
                "principal_id": principal.principal_id,
                "edges": len(edges),
                "delegation_edges": sum(1 for e in edges if e.kind == "delegation"),
                "parliament_sessions": gaps.parliament_sessions,
                "duration_ms": duration_ms,
            }},
        )
        return web.json_response(payload)

    async def _handle_login(self, request: Any) -> Any:
        """`POST /api/v1/login` — exchange username+password for the bearer token.

        **IT DOES NOT ADD A SECOND WAY TO AUTHENTICATE.** The API routes keep
        the one credential they have always had; this is the HUMAN way to obtain
        it, so a person opens a login form instead of running a CLI command and
        pasting a secret. One authenticator, one `_guard`, one token — the
        "never a second engine" rule applied to authority.

        IT IS THE ONE ROUTE THAT MUST NOT GO THROUGH `_guard`, because `_guard`
        demands the credential this route exists to hand out. It keeps the
        ORIGIN check — without it any page on the internet could POST here from
        a browser that can reach loopback and read the token out of the reply —
        and drops only the bearer check. That asymmetry is the whole security
        argument and is pinned by a test.

        BOTH FIELDS ARE COMPARED, AND BOTH ARE ALWAYS COMPARED — in
        `_prove_owner`, the one proof this route and the password change share.

        NO TOKEN FOR A PUBLISHED CREDENTIAL (Q29, L1). An install with no stored
        password answers 409 `setup_required` and hands out nothing; the owner
        proves ownership with the one-time setup code instead. An unreadable store
        answers 503 with a remedy and is never read as "no password" (L3).
        """
        web = _web()
        t0 = _time.monotonic()
        log.control_plane.info("[control_plane] server.login: entry")

        refusal = self._origin_ok(request, "login")
        if refusal is not None:
            return refusal

        # THE BRAKE, and it arrives with the widened bind rather than after it.
        # DEBT-310 shipped this route naming "no rate limit" as the thing that
        # "becomes real the moment ESC-172 is answered 'widen the bind'". It was
        # answered on 2026-09-12, so the brake is in the same change. It REFUSES
        # rather than sleeping: a handler that waits holds a worker, which hands
        # an attacker an amplifier instead of a limit.
        source = self._request_source(request)
        if self._login_attempts.is_refused(source):
            return self._too_many("login", source)

        fields, bad_body = await self._credential_fields(
            request, "login", ("username", "password")
        )
        if fields is None:
            return bad_body

        # 2. DECISION — which of the three states is this install in?
        state = await asyncio.to_thread(self._password.lookup)
        if state.state == "unavailable":
            return self._store_unavailable("login", state.remedy)
        if state.state == "setup":
            self._spawn_setup_code("login")
            log.control_plane.info(
                "[control_plane] server.login: exit — no token, the dashboard has no "
                "password yet (setup mode)",
                extra={"_fields": {"source": source}},
            )
            return web.json_response({"error": "setup_required"}, status=409)

        # 3. STEP — the one proof.
        outcome = await self._prove_owner(
            source, fields["username"], fields["password"], state
        )
        if outcome == "refused":
            return self._too_many("login", source)
        if outcome == "wrong":
            log.control_plane.warning(
                "[control_plane] server.login: exit — refused, credentials did "
                "not match",
                extra={"_fields": {
                    "source": source,
                    # The USERNAME is not logged. A failed login is often a typo
                    # in the password, and a log line carrying the attempted
                    # username turns the journal into a place credentials leak.
                    "duration_ms": (_time.monotonic() - t0) * 1000,
                    "remedy": "the username is control_plane.username in stackowl.yaml; "
                              "a forgotten password is reset with `stackowl "
                              "control-plane reset-password` on the host",
                }},
            )
            return web.json_response({"error": "invalid credentials"}, status=401)

        # 4. EXIT
        log.control_plane.info(
            "[control_plane] server.login: exit — granted",
            extra={"_fields": {
                "username": self._settings.control_plane.username,
                "duration_ms": (_time.monotonic() - t0) * 1000,
            }},
        )
        return web.json_response({"token": self._token})

    async def _handle_setup(self, request: Any) -> Any:
        """`POST /api/v1/setup` — prove ownership with the setup code; set the first password.

        THE ONLY WAY OUT OF SETUP MODE (Q29). The code is what proves ownership:
        only someone with the host's terminal or the owner's Telegram holds it,
        which no published password can say. It keeps the ORIGIN check, counts
        every attempt toward the same brake as a sign-in, and answers a wrong,
        expired or used code with the uniform 401.

        A WEAK PASSWORD DOES NOT USE THE CODE UP, so the owner fixes the password
        and tries again. Success rotates the token and returns the new one — the
        only token that opens anything afterwards. Serialised with the password
        change, so two at once cannot both win.
        """
        web = _web()
        t0 = _time.monotonic()
        log.control_plane.info("[control_plane] server.setup: entry")

        refusal = self._origin_ok(request, "setup")
        if refusal is not None:
            return refusal
        source = self._request_source(request)
        if self._login_attempts.is_refused(source):
            return self._too_many("setup", source)
        fields, bad_body = await self._credential_fields(
            request, "setup", ("code", "new_password")
        )
        if fields is None:
            return bad_body

        async with self._credential_lock:
            # 2. DECISION — re-read INSIDE the lock: a setup that just won made
            # this install ready, and the loser must see that.
            state = await asyncio.to_thread(self._password.lookup)
            if state.state == "unavailable":
                return self._store_unavailable("setup", state.remedy)
            try:
                outcome = await self._prove_setup_code(source, fields["code"], state)
            except PasswordStoreUnavailable as exc:
                return self._store_unavailable("setup", exc.remedy)
            if outcome == "refused":
                return self._too_many("setup", source)
            if outcome == "wrong":
                log.control_plane.warning(
                    "[control_plane] server.setup: exit — refused, the setup code is "
                    "wrong, expired or already used; nothing was stored",
                    extra={"_fields": {"source": source, "remedy": RESET_REMEDY}},
                )
                return self._reject(web)
            reason = weakness(fields["new_password"], self._settings.control_plane.username)
            if reason is not None:
                log.control_plane.warning(
                    "[control_plane] server.setup: exit — refused, the new password is "
                    "not acceptable; the setup code was NOT used",
                    extra={"_fields": {"source": source, "reason": reason}},
                )
                return web.json_response(
                    {"error": "weak_password", "reason": reason}, status=400
                )
            # 3. STEP
            response = await self._replace_password(
                "setup", fields["new_password"], consume_code=True
            )

        # 4. EXIT
        log.control_plane.info(
            "[control_plane] server.setup: exit",
            extra={"_fields": {
                "source": source,
                "status": response.status,
                "duration_ms": (_time.monotonic() - t0) * 1000,
            }},
        )
        return response

    async def _handle_password(self, request: Any) -> Any:
        """`POST /api/v1/password` — change the dashboard password.

        IT AUTHENTICATES LIKE LOGIN, NOT THROUGH `_guard`: it re-proves the
        username and CURRENT password through `_prove_owner`, counted toward the
        same brake, rather than trusting a bearer token a stolen tab could carry.
        Success rotates the token, so every tab signed in before the change is
        signed out by it. Serialised with setup, so two changes at once cannot
        both win and the token and hash always agree.
        """
        web = _web()
        t0 = _time.monotonic()
        log.control_plane.info("[control_plane] server.password: entry")

        refusal = self._origin_ok(request, "password")
        if refusal is not None:
            return refusal
        source = self._request_source(request)
        if self._login_attempts.is_refused(source):
            return self._too_many("password", source)
        fields, bad_body = await self._credential_fields(
            request, "password", ("username", "current_password", "new_password")
        )
        if fields is None:
            return bad_body

        # 2. DECISION — PROVE FIRST, OUTSIDE THE LOCK. scrypt takes tens of
        # milliseconds, and an unauthenticated guess holding the lock would make
        # the owner's own change wait behind it.
        state = await asyncio.to_thread(self._password.lookup)
        if state.state == "unavailable":
            return self._store_unavailable("password", state.remedy)
        if state.state == "setup":
            log.control_plane.info(
                "[control_plane] server.password: exit — there is no password to "
                "change yet (setup mode)",
                extra={"_fields": {"source": source}},
            )
            return web.json_response({"error": "setup_required"}, status=409)
        outcome = await self._prove_owner(
            source, fields["username"], fields["current_password"], state
        )
        if outcome == "refused":
            return self._too_many("password", source)
        if outcome == "wrong":
            log.control_plane.warning(
                "[control_plane] server.password: exit — refused, the current "
                "credentials did not match",
                extra={"_fields": {
                    "source": source,
                    "duration_ms": (_time.monotonic() - t0) * 1000,
                }},
            )
            return self._reject(web)
        reason = weakness(fields["new_password"], self._settings.control_plane.username)
        if reason is not None:
            log.control_plane.warning(
                "[control_plane] server.password: exit — refused, the new password "
                "is not acceptable; nothing was stored",
                extra={"_fields": {"source": source, "reason": reason}},
            )
            return web.json_response(
                {"error": "weak_password", "reason": reason}, status=400
            )

        async with self._credential_lock:
            # RE-CHECK INSIDE THE LOCK. A change that won while this one was proving
            # replaced the record the proof was made against, so this proof is stale.
            fresh = await asyncio.to_thread(self._password.lookup)
            if fresh.state == "unavailable":
                return self._store_unavailable("password", fresh.remedy)
            if fresh.state != "ready" or fresh.record != state.record:
                log.control_plane.warning(
                    "[control_plane] server.password: exit — refused, the password "
                    "changed while this request was being checked",
                    extra={"_fields": {"source": source}},
                )
                return self._reject(web)
            # 3. STEP
            response = await self._replace_password(
                "password", fields["new_password"], consume_code=False
            )

        # 4. EXIT
        log.control_plane.info(
            "[control_plane] server.password: exit",
            extra={"_fields": {
                "source": source,
                "status": response.status,
                "duration_ms": (_time.monotonic() - t0) * 1000,
            }},
        )
        return response

    async def _handle_agents(self, request: Any) -> Any:
        """`GET /api/v1/agents` — every agent's card, its AUTHORITY, and its runtime.

        A05.3's gap, corrected: an owl's `bounds` is rendered by no surface at
        all. `/owls list` shows display/role/status, `/owls menu` adds the tier,
        `owls_list` adds runtime, and the only non-write reference to
        `manifest.bounds` in `src/` is the YAML persister. "What may this agent
        do" has had no reader.

        **IT REPORTS BOTH SKILL ANSWERS, SEPARATELY NAMED, BECAUSE THEY
        DISAGREE.** `OwlAgentManifest.skills`'s own comment says it "records
        ownership" — the claim `skill_ownership` also makes. MEASURED
        2026-09-12: secretary declares **71** on its card against **9** rows in
        the table, and only **8** of the 71 name a skill that exists;
        rca_gatherer 31/4/1; scout 7/4/0; verifier declares 0 and owns 12.
        Merging them would pick a side in a disagreement the platform itself
        works around (`delivery_gate.py` reconciles "first owner wins"), and a
        single number would be wrong for nine of eleven owls.

        THE CARD COMES FROM `OwlStore`, NOT `OwlRegistry`: the registry is an
        in-memory rebuild with no `owner_id`, so joining it to owner-scoped rows
        would mix two populations. Same reason A04.1 took the store.
        """
        web = _web()
        t0 = _time.monotonic()
        log.control_plane.info("[control_plane] server.agents: entry")

        principal, refusal = await self._guard(request, "agents")
        if principal is None:
            return refusal

        if self._db is None:
            log.control_plane.warning(
                "[control_plane] server.agents: exit — no db wired, so there is "
                "nothing to report",
                extra={"_fields": {"principal_id": principal.principal_id}},
            )
            return web.json_response({"agents": [], "wired": False}, status=503)

        manifests = await OwlStore(self._db, principal.principal_id).list_all()
        owned = await read_all_skill_ownership(self._db)
        activity, _gaps = await read_owl_activity(
            self._db, manifests,
            owner_id=principal.principal_id,
            since_epoch=_time.time() - _ACTIVITY_WINDOW_S,
        )
        by_name = {a.manifest.name: a for a in activity}

        payload = {
            "wired": True,
            "agents": [
                {
                    "name": m.name,
                    "display_name": m.display,
                    "role": m.role,
                    "lifecycle": m.lifecycle,
                    "model_tier": m.model_tier,
                    "origin": m.origin,
                    # AUTHORITY, AND `unbounded` IS THE FIELD THAT MATTERS.
                    # `effective_bounds` is the platform's OWN fold, and its
                    # docstring says what a null result means: "with no defined
                    # term the result is None (genuinely unbounded)", and
                    # `check_effective_bounds` then reads "None effective bounds
                    # (no constraint anywhere) -> unrestricted". So this is
                    # COMPUTED by the same function the enforcement path calls,
                    # never inferred here — a surface that reasoned its way to
                    # this answer could disagree with the gate that enforces it.
                    # MEASURED 2026-09-12: SEVEN of eleven owls have neither
                    # bounds nor ceiling and are therefore unrestricted, and the
                    # live evidence agrees — secretary, verifier, rca_gatherer
                    # and hypothesis are each presented the WHOLE registry (79
                    # tools) while bounded owls see 6, 8 and 11. Of 98 bounds
                    # refusals in the retained logs, ZERO are on an unbounded owl.
                    "bounds": (
                        None if m.bounds is None
                        else m.bounds.model_dump(mode="json", exclude_none=True)
                    ),
                    "creation_ceiling": (
                        None if m.creation_ceiling is None
                        else m.creation_ceiling.model_dump(mode="json", exclude_none=True)
                    ),
                    "unbounded": effective_bounds(m.bounds, m.creation_ceiling) is None,
                    "skills_on_card": len(m.skills),
                    "skills_owned": len(owned.get(m.name, ())),
                    "in_flight": (
                        by_name[m.name].in_flight if m.name in by_name else 0
                    ),
                    "recent_turns": (
                        by_name[m.name].recent_turns if m.name in by_name else 0
                    ),
                }
                for m in manifests
            ],
        }

        disagreeing = sum(
            1 for m in manifests if len(m.skills) != len(owned.get(m.name, ()))
        )
        duration_ms = (_time.monotonic() - t0) * 1000
        log.control_plane.info(
            "[control_plane] server.agents: exit — served",
            extra={"_fields": {
                "principal_id": principal.principal_id,
                "agents": len(manifests),
                "unbounded": sum(
                    1 for m in manifests
                    if effective_bounds(m.bounds, m.creation_ceiling) is None
                ),
                "skill_counts_disagree": disagreeing,
                "duration_ms": duration_ms,
            }},
        )
        return web.json_response(payload)

    async def _handle_tasks(self, request: Any) -> Any:
        """`GET /api/v1/tasks` — unfinished work as a SET, with a reason per row.

        A05.6's gap is that work is visible ONE ROW AT A TIME and only to
        somebody who already knows the id. The JOBS half shipped with A05.4
        (`/api/v1/schedules`); this is the TASKS half.

        IT CARRIES `blocked`, AND THAT IS THE POINT. MEASURED 2026-09-12: five
        `secretary` rows sit `pending` with `next_attempt_at` ten hours in the
        past and a `last_error` set. Rendered as status alone that is a wedged
        platform, and the operator's next move is a restart that changes
        nothing. Every one is a sub-task of a TERMINAL parent, which the loop
        deliberately will not run. A surface that cannot say so invents an
        alarm — worse than the silence it replaces.
        """
        web = _web()
        t0 = _time.monotonic()
        log.control_plane.info("[control_plane] server.tasks: entry")

        principal, refusal = await self._guard(request, "tasks")
        if principal is None:
            return refusal

        if self._db is None:
            log.control_plane.warning(
                "[control_plane] server.tasks: exit — no db wired, so there is "
                "nothing to report",
                extra={"_fields": {"principal_id": principal.principal_id}},
            )
            return web.json_response({"tasks": [], "wired": False}, status=503)

        rows, gaps = await read_task_activity(
            self._db,
            owner_id=principal.principal_id,
            now_iso=_dt.datetime.now(_dt.UTC).isoformat(),
        )
        payload = {
            "wired": True,
            "unseen_other_owner": gaps.unseen_other_owner,
            "truncated": gaps.truncated,
            # WHAT THE TERMINAL FILTER TOOK OUT. Until 2026-09-12 this route
            # served dead-lettered rows inside `tasks` — 82 rows where 5 were
            # live, 72 of them created in one batch 23 days earlier. Removing
            # them from the SET without reporting the COUNT would trade a false
            # alarm for a silent omission, and `dead_letter` is the one ending
            # this platform promises never to prune.
            "dead_lettered": gaps.dead_lettered,
            "newest_dead_letter_at": gaps.newest_dead_letter_at,
            "tasks": [
                {
                    "task_id": t.task_id,
                    "owl_name": t.owl_name,
                    "status": t.status,
                    "blocked": t.blocked,
                    "attempt_count": t.attempt_count,
                    "max_attempts": t.max_attempts,
                    "next_attempt_at": t.next_attempt_at,
                    "goal": t.goal,
                    "last_error": t.last_error,
                }
                for t in rows
            ],
        }

        duration_ms = (_time.monotonic() - t0) * 1000
        log.control_plane.info(
            "[control_plane] server.tasks: exit — served",
            extra={"_fields": {
                "principal_id": principal.principal_id,
                "unfinished": len(rows),
                "blocked_pending": sum(
                    1 for t in rows if t.status == "pending" and t.blocked != "none"
                ),
                "unseen_other_owner": gaps.unseen_other_owner,
                "duration_ms": duration_ms,
            }},
        )
        return web.json_response(payload)

    async def _handle_config(self, request: Any) -> Any:
        """`GET /api/v1/config` — every configured setting, as a SET, masked.

        IT READS THROUGH THE CHAT COMMAND'S OWN FOUR HELPERS —
        `config_path`, `load_yaml`, `collect_sensitive`, `flatten` — so this
        surface and `/config list` cannot disagree about what is configured or
        about what is secret. A second masking list is how a credential reaches
        an HTTP response, and DEBT-309 is the record of what the first one cost.

        SHIPPING THIS BEFORE DEBT-309 WOULD HAVE PUT SEVEN CREDENTIAL FIELDS ON
        A NETWORK SURFACE. `providers[].api_key`, `webhook.sources[].secret`,
        `mcp_server.auth_token` and four more carried no `sensitive=True`
        marker, and `flatten` did not descend into lists at all. That is why the
        masking was fixed first and this route second, in that order.

        IT SHOWS WHAT IS CONFIGURED, NOT WHAT IS EFFECTIVE. `load_yaml` reads
        the FILE, so a default the operator never set does not appear — exactly
        as `/config list` behaves. Making this surface show resolved defaults
        would be a second answer to one question; if that is wanted, it belongs
        in the shared helpers where the chat surface gets it too.
        """
        web = _web()
        t0 = _time.monotonic()
        log.control_plane.info("[control_plane] server.config: entry")

        principal, refusal = await self._guard(request, "config")
        if principal is None:
            return refusal

        path = config_path()
        if not path.exists():
            log.control_plane.warning(
                "[control_plane] server.config: exit — no config file on disk",
                extra={"_fields": {"principal_id": principal.principal_id,
                                   "path": str(path)}},
            )
            return web.json_response({"settings": [], "wired": False}, status=503)

        sensitive: set[str] = set()
        collect_sensitive(Settings, "", sensitive)
        pairs: list[tuple[str, str]] = []
        flatten("", load_yaml(path), sensitive, pairs)
        pairs.sort(key=lambda kv: kv[0])

        masked = sum(1 for _key, value in pairs if value == "***")
        payload = {
            "wired": True,
            "settings": [{"key": k, "value": v, "masked": v == "***"} for k, v in pairs],
        }
        duration_ms = (_time.monotonic() - t0) * 1000
        log.control_plane.info(
            "[control_plane] server.config: exit — served",
            extra={"_fields": {
                "principal_id": principal.principal_id,
                "settings": len(pairs),
                "masked": masked,
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

        principal, refusal = await self._guard(request, "health")
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

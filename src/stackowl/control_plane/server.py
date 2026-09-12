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
from typing import TYPE_CHECKING, Any

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
)
from stackowl.control_plane.page import INDEX_HTML
from stackowl.infra.observability import log
from stackowl.owls.activity import read_owl_activity
from stackowl.owls.skill_ownership import read_all_skill_ownership
from stackowl.owls.store import OwlStore
from stackowl.pipeline.durable.activity import read_task_activity

if TYPE_CHECKING:
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool
    from stackowl.health.aggregator import HealthAggregator
    from stackowl.scheduler.scheduler import JobScheduler

from stackowl.supervisor.supervisor import SupervisedTask

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
    ) -> None:
        log.control_plane.info(
            "[control_plane] server.init: entry",
            extra={"_fields": {
                "has_health": health is not None,
                "has_scheduler": scheduler is not None,
                "has_db": db is not None,
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
        app.router.add_get("/api/v1/health", self._handle_health)
        app.router.add_get("/api/v1/schedules", self._handle_schedules)
        app.router.add_get("/api/v1/config", self._handle_config)
        app.router.add_get("/api/v1/skills", self._handle_skills)
        app.router.add_get("/api/v1/tasks", self._handle_tasks)
        app.router.add_get("/api/v1/agents", self._handle_agents)
        app.router.add_post("/api/v1/login", self._handle_login)

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
        self._warn_if_default_credentials()

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

    def _warn_if_default_credentials(self) -> None:
        """Say it at boot while the login is still admin/admin.

        A default credential is only as dangerous as the bind, and the bind is
        ONE SETTING away from a network — which is exactly what ESC-172 is open
        about. So the pairing is what gets logged: an operator who widens the
        bind and greps for this line finds it, and the message names the remedy
        rather than the problem.

        WARNING, not INFO, and the level is the point: production runs at INFO
        and this deployment has written ZERO debug records in 677,108, so a
        DEBUG line here would be a warning nobody could ever read. It is also
        reported to the PAGE on every successful sign-in, because the person who
        can fix it is the one looking at the dashboard, not the one reading the
        journal.
        """
        cfg = self._settings.control_plane
        if not cfg.credentials_are_default:
            return
        loopback = cfg.bind_address in ("127.0.0.1", "localhost", "::1")
        log.control_plane.warning(
            "[control_plane] server.run: the dashboard login is still the "
            "DEFAULT admin/admin",
            extra={"_fields": {
                "bind": cfg.bind_address,
                # The pair is what matters. Default credentials on loopback are
                # a note; default credentials on 0.0.0.0 are an open door.
                "reachable_off_this_machine": not loopback,
                "remedy": "set control_plane.username and "
                          "control_plane.password in stackowl.yaml",
            }},
        )

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
        cfg = self._settings.control_plane
        if check_origin(
            request.headers.get("Origin"),
            request.headers.get("Host"),
            f"{cfg.bind_address}:{cfg.port}",
        ):
            return None
        log.control_plane.warning(
            f"[control_plane] server.{route}: exit — refused, cross-origin",
            extra={"_fields": {"origin": request.headers.get("Origin")}},
        )
        return self._reject(_web())

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

        refusal = self._origin_ok(request, route)
        if refusal is not None:
            return None, refusal

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

        principal, refusal = self._guard(request, "skills")
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

        BOTH FIELDS ARE COMPARED IN CONSTANT TIME, AND BOTH ARE ALWAYS COMPARED.
        Returning early on an unknown username makes the response time a
        username oracle; `hmac.compare_digest` on each, with the results
        combined afterwards, keeps a wrong username and a wrong password
        indistinguishable from outside.
        """
        web = _web()
        t0 = _time.monotonic()
        log.control_plane.info("[control_plane] server.login: entry")
        cfg = self._settings.control_plane

        refusal = self._origin_ok(request, "login")
        if refusal is not None:
            return refusal

        try:
            body = await request.json()
        except Exception as exc:  # noqa: BLE001 — B5, never silent
            log.control_plane.warning(
                "[control_plane] server.login: exit — refused, unreadable body",
                exc_info=exc,
            )
            return web.json_response({"error": "expected a JSON body"}, status=400)

        presented_user = str((body or {}).get("username") or "")
        presented_pass = str((body or {}).get("password") or "")

        user_ok = _hmac.compare_digest(presented_user, cfg.username)
        pass_ok = _hmac.compare_digest(presented_pass, cfg.password)
        if not (user_ok and pass_ok):
            duration_ms = (_time.monotonic() - t0) * 1000
            log.control_plane.warning(
                "[control_plane] server.login: exit — refused, credentials did "
                "not match",
                extra={"_fields": {
                    # The USERNAME is not logged. A failed login is often a typo
                    # in the password, and a log line carrying the attempted
                    # username turns the journal into a place credentials leak.
                    "duration_ms": duration_ms,
                    "remedy": "set control_plane.username and "
                              "control_plane.password in stackowl.yaml",
                }},
            )
            return web.json_response({"error": "invalid credentials"}, status=401)

        duration_ms = (_time.monotonic() - t0) * 1000
        log.control_plane.info(
            "[control_plane] server.login: exit — granted",
            extra={"_fields": {
                "username": cfg.username,
                "default_credentials": cfg.credentials_are_default,
                "duration_ms": duration_ms,
            }},
        )
        return web.json_response({
            "token": self._token,
            # THE PAGE IS TOLD, not just the log. A warning only an operator who
            # greps the journal can see is the DEBUG-evidence failure wearing a
            # different level — the person who can fix this is the one looking
            # at the dashboard.
            "default_credentials": cfg.credentials_are_default,
        })

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

        principal, refusal = self._guard(request, "agents")
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

        principal, refusal = self._guard(request, "tasks")
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

        principal, refusal = self._guard(request, "config")
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

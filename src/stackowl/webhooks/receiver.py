"""WebhookReceiver — HTTP-in / scheduler-enqueue bridge (Story 7.5).

* Binds an aiohttp server on ``settings.webhook.bind_address:port``
* Accepts ``POST /webhook/{source}`` requests
* Rejects unknown sources, invalid signatures, and rate-limited callers
* Enqueues a one-shot ``webhook_handler`` job via the scheduler

Security posture:
* Signatures verified with :func:`hmac.compare_digest` (constant-time)
* Rate-limit logs never include request bodies or headers
* Source secrets are resolved per-request via :class:`SecretResolver`
"""

from __future__ import annotations

import json
import time as _time
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

from stackowl.config.settings import Settings
from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.memory.bridge import HealthReport
from stackowl.supervisor.supervisor import SupervisedTask
from stackowl.webhooks.rate_limit import TokenBucket
from stackowl.webhooks.receiver_helpers import (
    claim_delivery,
    derive_event_id,
    make_event_id,
    now_iso,
    resolve_source_secret,
    validate_hmac_signature,
    validate_signed_timestamp,
)

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.config.webhook_settings import WebhookSourceConfig
    from stackowl.db.pool import DbPool
    from stackowl.scheduler.scheduler import JobScheduler


def _import_aiohttp_web() -> Any:
    """Lazy import so the module is importable even without aiohttp installed."""
    try:
        from aiohttp import web as _web  # noqa: PLC0415

        return _web
    except ImportError as exc:
        raise ImportError(
            "aiohttp is required for WebhookReceiver — install with `uv add aiohttp`"
        ) from exc


# F139: single generic rejection used for BOTH unknown/disabled source and
# invalid signature, so the two cases are indistinguishable to a caller and a
# source id cannot be enumerated. Internal logs still record which check failed.
_UNAUTHORIZED_STATUS = 401
_UNAUTHORIZED_BODY = "unauthorized"


def _response(web: Any, status: int, text: str, *, json_body: bool = False) -> Any:
    if json_body:
        return web.Response(status=status, content_type="application/json", text=text)
    return web.Response(status=status, text=text)


class WebhookEvent(BaseModel):
    """Normalised webhook event handed to the queued scheduler job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    source: str
    payload: dict[str, Any]
    received_at: str


class WebhookReceiver(SupervisedTask):
    """HTTP server that receives, validates, and enqueues webhook events."""

    def __init__(
        self,
        scheduler: JobScheduler,
        settings: Settings,
        db: DbPool | None = None,
        rate_limiter: TokenBucket | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._settings = settings
        self._db = db
        self._rate_limiter = rate_limiter or TokenBucket()
        self._clock: Clock = clock or WallClock()
        self._runner: Any | None = None
        self._site: Any | None = None
        self._bound = False

    @property
    def task_id(self) -> str:
        return "webhook_receiver"

    async def run(self) -> None:
        TestModeGuard.assert_not_test_mode("webhook_receiver.bind")
        web = _import_aiohttp_web()

        log.webhook.info(
            "[webhook] receiver.run: entry",
            extra={
                "_fields": {
                    "bind": self._settings.webhook.bind_address,
                    "port": self._settings.webhook.port,
                    "sources": len(self._settings.webhook.sources),
                }
            },
        )
        app = web.Application()
        app.router.add_post("/webhook/{source}", self._handle_request)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(
            runner,
            self._settings.webhook.bind_address,
            self._settings.webhook.port,
        )
        try:
            await site.start()
        except Exception as exc:  # B5 — never silent
            log.webhook.error(
                "[webhook] receiver.run: site bind failed",
                exc_info=exc,
                extra={"_fields": {"port": self._settings.webhook.port}},
            )
            raise

        self._runner = runner
        self._site = site
        self._bound = True
        log.webhook.info(
            "[webhook] receiver.run: exit — listening",
            extra={"_fields": {"port": self._settings.webhook.port}},
        )

    async def stop(self) -> None:
        """Tear down the HTTP server.  Idempotent."""
        log.webhook.debug("[webhook] receiver.stop: entry")
        if self._site is not None:
            try:
                await self._site.stop()
            except Exception as exc:  # B5 — never silent
                log.webhook.warning(
                    "[webhook] receiver.stop: site stop failed", exc_info=exc
                )
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception as exc:  # B5 — never silent
                log.webhook.warning(
                    "[webhook] receiver.stop: runner cleanup failed", exc_info=exc
                )
        self._site = None
        self._runner = None
        self._bound = False
        log.webhook.info("[webhook] receiver.stop: exit")

    def apply_settings(self, settings: Settings) -> None:
        """Hot-swap the sources dict the running receiver reads per-request.

        Only ``sources`` is genuinely hot-reload-capable (schema-declared in
        webhook_settings.py) — bind_address/port/the top-level enabled flag
        require a real restart to take effect (a brand-new listener bind),
        which this method does NOT attempt.
        """
        old_count = len(self._settings.webhook.sources)
        self._settings = settings
        log.webhook.info(
            "[webhook] receiver.apply_settings: sources refreshed",
            extra={
                "_fields": {
                    "old_count": old_count,
                    "new_count": len(settings.webhook.sources),
                }
            },
        )

    async def health(self) -> HealthReport:
        log.webhook.debug("[webhook] receiver.health: entry")
        status: Literal["ok", "degraded", "down"] = "ok" if self._bound else "down"
        return HealthReport(
            name="webhook.receiver",
            status=status,
            details={
                "bound": self._bound,
                "sources": len(self._settings.webhook.sources),
            },
        )

    # ------------------------------------------------------------------ handler

    async def _handle_request(self, request: Any) -> Any:
        web = _import_aiohttp_web()
        t0 = _time.monotonic()
        source = request.match_info.get("source", "")
        remote = request.remote or "unknown"
        log.webhook.debug(
            "[webhook] receiver.handle: entry",
            extra={"_fields": {"source": source, "remote": remote}},
        )

        # 1. RATE LIMIT — bucket per (remote, source); never log bodies
        if not self._rate_limiter.consume(f"{remote}:{source}"):
            duration_ms = (_time.monotonic() - t0) * 1000
            log.webhook.warning(
                "[webhook] receiver.handle: rate-limited",
                extra={"_fields": {"source": source, "duration_ms": duration_ms}},
            )
            return _response(web, 429, "rate limit exceeded")

        # 2. SOURCE LOOKUP
        # F139: an unknown/disabled source and an invalid signature MUST be
        # indistinguishable to the caller (same status + body), else an attacker
        # can enumerate which source ids are configured. The HTTP response is the
        # generic ``_UNAUTHORIZED`` reject; the LOG still records exactly which
        # check failed for operators.
        cfg = self._settings.webhook.sources.get(source)
        if cfg is None or not cfg.enabled:
            log.webhook.warning(
                "[webhook] receiver.handle: unknown or disabled source",
                extra={"_fields": {"source": source}},
            )
            return self._reject(web)

        # 3. TIMESTAMP-WINDOW + SIGNATURE — constant-time HMAC (over ts+body when
        # the source opts into the signed-timestamp scheme, else body-only legacy).
        body = await request.read()
        signed_timestamp = ""
        if cfg.timestamp_header:
            signed_timestamp = request.headers.get(cfg.timestamp_header, "")
        delivery_id = ""
        if cfg.delivery_id_header:
            delivery_id = request.headers.get(cfg.delivery_id_header, "")
        if not await self._signature_ok(source, cfg, body, request, signed_timestamp):
            # F139: identical reject to the unknown-source path above.
            return self._reject(web)
        log.webhook.debug(
            "[webhook] receiver.handle: signature validated",
            extra={"_fields": {"source": source}},
        )

        # 4. PARSE + DEDUP-CLAIM + ENQUEUE
        return await self._parse_and_enqueue(
            source, cfg, body, signed_timestamp, delivery_id, web, t0
        )

    def _reject(self, web: Any) -> Any:
        """Uniform unauthorized rejection (F139) — same status + body always."""
        return _response(web, _UNAUTHORIZED_STATUS, _UNAUTHORIZED_BODY)

    async def _signature_ok(
        self,
        source: str,
        cfg: WebhookSourceConfig,
        body: bytes,
        request: Any,
        signed_timestamp: str,
    ) -> bool:
        provided = request.headers.get("X-Webhook-Signature", "")
        secret = resolve_source_secret(source, cfg)
        if secret is None:
            return False
        # Signed-timestamp scheme (preferred): rejects stale/tampered ts AND
        # verifies the HMAC over ts+body in one constant-time check.
        if cfg.timestamp_header:
            ok = validate_signed_timestamp(
                secret,
                signed_timestamp,
                body,
                provided,
                cfg.replay_tolerance_s,
                self._clock,
            )
            if not ok:
                log.webhook.warning(
                    "[webhook] receiver.handle: invalid signed timestamp/signature",
                    extra={"_fields": {"source": source}},
                )
            return ok
        # Legacy body-only HMAC retained for existing senders (dedup via the
        # delivery-id header, enforced at config load).
        if not validate_hmac_signature(secret, body, provided):
            log.webhook.warning(
                "[webhook] receiver.handle: invalid signature",
                extra={"_fields": {"source": source}},
            )
            return False
        return True

    async def _parse_and_enqueue(
        self,
        source: str,
        cfg: WebhookSourceConfig,
        body: bytes,
        signed_timestamp: str,
        delivery_id: str,
        web: Any,
        t0: float,
    ) -> Any:
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:  # B5
            log.webhook.warning(
                "[webhook] receiver.handle: invalid JSON body",
                exc_info=exc,
                extra={"_fields": {"source": source, "body_len": len(body)}},
            )
            return _response(web, 400, "invalid json body")
        if not isinstance(payload, dict):
            log.webhook.warning(
                "[webhook] receiver.handle: payload not an object",
                extra={"_fields": {"source": source, "type": type(payload).__name__}},
            )
            return _response(web, 400, "payload must be a json object")

        received_at = now_iso()
        # Server-derived, attacker-immutable dedup id (R5). When the source uses
        # the signed-timestamp scheme the id is derived from source+ts+body so a
        # captured replay collides; otherwise it stays a fresh uuid trace id and
        # dedup leans on the delivery-id header (config-enforced) — folded in here.
        if signed_timestamp:
            event_id = derive_event_id(source, signed_timestamp, body)
        elif delivery_id:
            # Body-only legacy path: dedup keys off the sender delivery-id +
            # body (folded with source). Config guarantees one mechanism exists.
            event_id = derive_event_id(source, delivery_id, body)
        else:
            # Defensive fallback (config-validate should prevent reaching here):
            # a fresh uuid so a missing-id request is never silently deduped away.
            event_id = make_event_id()
        event = WebhookEvent(
            event_id=event_id, source=source, payload=payload, received_at=received_at
        )

        # Dedup-claim BEFORE enqueue (R7): fail-closed CAS over the derived id.
        # rowcount==0 → replay → 200 deduplicated, NO side-effecting job.
        if self._db is not None:
            try:
                won = await claim_delivery(self._db, event_id, source, received_at)
            except Exception as exc:  # B5 — fail-closed: a gate that swallows is not a gate
                log.webhook.error(
                    "[webhook] receiver.handle: dedup claim failed — rejecting",
                    exc_info=exc,
                    extra={"_fields": {"source": source}},
                )
                return _response(web, 500, "dedup claim failed")
            if not won:
                duration_ms = (_time.monotonic() - t0) * 1000
                log.webhook.info(
                    "[webhook] receiver.handle: replay deduplicated — no enqueue",
                    extra={"_fields": {"source": source, "duration_ms": duration_ms}},
                )
                return _response(
                    web, 200, json.dumps({"deduplicated": True}), json_body=True
                )
        else:
            # DB None → replay-dedup disabled; the timestamp window still bounds
            # replay to ±tolerance. Never pretend protection is active (R7).
            log.webhook.warning(
                "[webhook] receiver.handle: no DB — replay-dedup disabled, "
                "window-only protection",
                extra={"_fields": {"source": source}},
            )

        try:
            await self._scheduler.create_job(
                handler_name="webhook_handler",
                schedule="@once",
                idempotency_key=f"webhook:{event_id}",
                params={"event": event.model_dump()},
            )
        except Exception as exc:  # B5 — never silent
            log.webhook.error(
                "[webhook] receiver.handle: enqueue failed",
                exc_info=exc,
                extra={"_fields": {"event_id": event_id, "source": source}},
            )
            return _response(web, 500, "enqueue failed")

        duration_ms = (_time.monotonic() - t0) * 1000
        log.webhook.info(
            "[webhook] receiver.handle: event enqueued",
            extra={
                "_fields": {
                    "event_id": event_id,
                    "source": source,
                    "duration_ms": duration_ms,
                }
            },
        )
        log.webhook.debug(
            "[webhook] receiver.handle: exit",
            extra={"_fields": {"status": 202, "duration_ms": duration_ms}},
        )
        return _response(
            web, 202, json.dumps({"event_id": event_id}), json_body=True
        )

    # ----------------------------------------------------------- test helpers

    async def _validate_signature(self, secret: str, body: bytes, provided_sig: str) -> bool:
        """Public-ish wrapper kept for the test suite."""
        return validate_hmac_signature(secret, body, provided_sig)

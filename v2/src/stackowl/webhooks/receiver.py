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

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.memory.bridge import HealthReport
from stackowl.supervisor.supervisor import SupervisedTask
from stackowl.webhooks.rate_limit import TokenBucket
from stackowl.webhooks.receiver_helpers import (
    make_event_id,
    now_iso,
    persist_event_log,
    resolve_source_secret,
    validate_hmac_signature,
)

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.config.settings import Settings
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
    ) -> None:
        self._scheduler = scheduler
        self._settings = settings
        self._db = db
        self._rate_limiter = rate_limiter or TokenBucket()
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
        cfg = self._settings.webhook.sources.get(source)
        if cfg is None or not cfg.enabled:
            log.webhook.warning(
                "[webhook] receiver.handle: unknown or disabled source",
                extra={"_fields": {"source": source}},
            )
            return _response(web, 404, "unknown source")

        # 3. SIGNATURE — constant-time HMAC
        body = await request.read()
        if not await self._signature_ok(source, cfg, body, request):
            return _response(web, 400, "invalid signature")
        log.webhook.debug(
            "[webhook] receiver.handle: signature validated",
            extra={"_fields": {"source": source}},
        )

        # 4. PARSE + ENQUEUE
        return await self._parse_and_enqueue(source, body, web, t0)

    async def _signature_ok(
        self,
        source: str,
        cfg: WebhookSourceConfig,
        body: bytes,
        request: Any,
    ) -> bool:
        provided = request.headers.get("X-Webhook-Signature", "")
        secret = resolve_source_secret(source, cfg)
        if secret is None:
            return False
        if not validate_hmac_signature(secret, body, provided):
            log.webhook.warning(
                "[webhook] receiver.handle: invalid signature",
                extra={"_fields": {"source": source}},
            )
            return False
        return True

    async def _parse_and_enqueue(
        self, source: str, body: bytes, web: Any, t0: float
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

        event_id = make_event_id()
        received_at = now_iso()
        event = WebhookEvent(
            event_id=event_id, source=source, payload=payload, received_at=received_at
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

        if self._db is not None:
            await persist_event_log(self._db, event_id, source, received_at)

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

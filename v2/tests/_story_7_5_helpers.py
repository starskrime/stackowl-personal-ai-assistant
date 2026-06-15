"""Shared helpers for Story 7.5 tests — kept in a non-``test_`` module."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from stackowl.config.notification_settings import NotificationSettings
from stackowl.config.settings import Settings
from stackowl.config.test_mode import TestModeGuard
from stackowl.config.webhook_settings import WebhookSettings, WebhookSourceConfig
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool


def disable_guard() -> None:
    TestModeGuard.deactivate()


async def open_db(tmp_path: Path) -> DbPool:
    db_path = tmp_path / f"test-{uuid.uuid4().hex[:6]}.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    return pool


def make_settings_with_webhooks(
    *,
    secret_env_var: str = "WEBHOOK_TEST_SECRET",
    source_name: str = "test_source",
    enabled: bool = True,
    max_per_hour: int = 10,
) -> Settings:
    """Build a Settings-like SimpleNamespace with one webhook source configured."""
    sources: dict[str, WebhookSourceConfig] = {}
    if source_name:
        # C7 / F132: every source must declare an anti-replay mechanism. These
        # body-only-HMAC tests keep the legacy signature path and declare a
        # delivery-id header for dedup (the validator requires ≥1 mechanism).
        sources[source_name] = WebhookSourceConfig(
            enabled=enabled,
            secret=secret_env_var,
            delivery_id_header="X-Delivery-Id",
        )
    ns = SimpleNamespace(
        notifications=NotificationSettings(max_notifications_per_hour=max_per_hour),
        webhook=WebhookSettings(
            enabled=True,
            bind_address="127.0.0.1",
            port=8766,
            sources=sources,
        ),
    )
    return cast(Settings, ns)


def make_signature(secret: str, body: bytes) -> str:
    """Return the canonical sha256-hex HMAC for ``body`` using ``secret``."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def make_mock_request(
    *,
    source: str,
    body: bytes,
    signature: str | None,
    remote: str = "127.0.0.1",
) -> Any:
    """Build an aiohttp-shaped MagicMock for handler unit-tests."""
    req = MagicMock()
    req.remote = remote
    req.match_info = {"source": source}
    headers: dict[str, str] = {}
    if signature is not None:
        headers["X-Webhook-Signature"] = signature
    req.headers = headers
    req.read = AsyncMock(return_value=body)
    return req


def make_mock_scheduler() -> Any:
    """Return a mock JobScheduler whose ``create_job`` is an AsyncMock."""
    scheduler = MagicMock()
    job_obj = MagicMock()
    job_obj.job_id = f"webhook_handler-{uuid.uuid4().hex[:8]}"
    scheduler.create_job = AsyncMock(return_value=job_obj)
    return scheduler


def iso_now() -> str:
    return datetime.now(UTC).isoformat()

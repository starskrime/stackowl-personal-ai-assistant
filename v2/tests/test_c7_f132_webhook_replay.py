"""C7 / F132 — webhook receiver must reject replays and stale/tampered timestamps.

Merge-gates (assert OUTCOMES):
* The SAME signed body POSTed twice → 1st enqueues, 2nd returns deduplicated and
  create_job is called EXACTLY ONCE (no second side-effecting job).
* A stale signed timestamp (now-400s, tolerance 300s) → HTTP 400, no enqueue.
* A tampered timestamp (valid body sig but ts not inside the HMAC) → HTTP 400.
* A WebhookSourceConfig with neither a timestamp nor a delivery-id mechanism →
  config validation error (fail-closed at load, R6).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from stackowl.config.notification_settings import NotificationSettings
from stackowl.config.settings import Settings
from stackowl.config.webhook_settings import WebhookSettings, WebhookSourceConfig
from stackowl.webhooks.receiver import WebhookReceiver
from tests._story_7_5_helpers import disable_guard, make_mock_request, make_mock_scheduler, open_db


def _signed_timestamp_sig(secret: str, ts: str, body: bytes) -> str:
    """Stripe-style HMAC over ``f"{ts}.".encode() + body``."""
    signed = f"{ts}.".encode() + body
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def _settings_with_timestamped_source(secret_env: str = "WEBHOOK_TEST_SECRET") -> Settings:
    src = WebhookSourceConfig(
        enabled=True,
        secret=secret_env,
        timestamp_header="X-Webhook-Timestamp",
        replay_tolerance_s=300,
    )
    ns = SimpleNamespace(
        notifications=NotificationSettings(max_notifications_per_hour=100),
        webhook=WebhookSettings(
            enabled=True, bind_address="127.0.0.1", port=8766,
            sources={"test_source": src},
        ),
    )
    return cast(Settings, ns)


def _request_with_ts(
    *, body: bytes, ts: str, sig: str, remote: str = "127.0.0.1"
) -> Any:
    req = make_mock_request(source="test_source", body=body, signature=sig, remote=remote)
    req.headers["X-Webhook-Timestamp"] = ts
    return req


class TestConfigValidatorFailClosed:
    def test_source_with_no_mechanism_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WebhookSourceConfig(enabled=True, secret="env:X")

    def test_timestamp_mechanism_accepted(self) -> None:
        cfg = WebhookSourceConfig(
            enabled=True, secret="env:X", timestamp_header="X-Webhook-Timestamp"
        )
        assert cfg.timestamp_header == "X-Webhook-Timestamp"

    def test_delivery_id_mechanism_accepted(self) -> None:
        cfg = WebhookSourceConfig(
            enabled=True, secret="env:X", delivery_id_header="X-Delivery-Id"
        )
        assert cfg.delivery_id_header == "X-Delivery-Id"


class TestTimestampWindow:
    async def test_stale_timestamp_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard()
        monkeypatch.setenv("WEBHOOK_TEST_SECRET", "shared")
        db = await open_db(tmp_path)
        try:
            receiver = WebhookReceiver(
                scheduler=make_mock_scheduler(),
                settings=_settings_with_timestamped_source(),
                db=db,
            )
            body = b'{"k":"v"}'
            stale = (datetime.now(UTC).timestamp()) - 400
            ts = datetime.fromtimestamp(stale, UTC).isoformat()
            sig = _signed_timestamp_sig("shared", ts, body)
            resp = await receiver._handle_request(_request_with_ts(body=body, ts=ts, sig=sig))
            # F139: stale-ts is a signature-verification failure → uniform 401
            # reject (was 400); still rejected with no enqueue.
            assert resp.status == 401
        finally:
            await db.close()

    async def test_tampered_timestamp_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard()
        monkeypatch.setenv("WEBHOOK_TEST_SECRET", "shared")
        db = await open_db(tmp_path)
        try:
            receiver = WebhookReceiver(
                scheduler=make_mock_scheduler(),
                settings=_settings_with_timestamped_source(),
                db=db,
            )
            body = b'{"k":"v"}'
            real_ts = datetime.now(UTC).isoformat()
            # Signature computed over the REAL ts, but the header carries a
            # different (attacker-substituted) ts → must reject.
            sig = _signed_timestamp_sig("shared", real_ts, body)
            forged_ts = datetime.now(UTC).isoformat()
            req = _request_with_ts(body=body, ts=forged_ts, sig=sig)
            resp = await receiver._handle_request(req)
            # If forged_ts happens to equal real_ts (same microsecond), skip.
            if forged_ts == real_ts:
                pytest.skip("timestamps collided")
            # F139: tampered-ts fails HMAC → uniform 401 reject (was 400).
            assert resp.status == 401
        finally:
            await db.close()


class TestReplayDedup:
    async def test_replay_enqueues_exactly_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard()
        monkeypatch.setenv("WEBHOOK_TEST_SECRET", "shared")
        db = await open_db(tmp_path)
        try:
            scheduler = make_mock_scheduler()
            receiver = WebhookReceiver(
                scheduler=scheduler,
                settings=_settings_with_timestamped_source(),
                db=db,
            )
            body = b'{"hello":"world"}'
            ts = datetime.now(UTC).isoformat()
            sig = _signed_timestamp_sig("shared", ts, body)

            r1 = await receiver._handle_request(_request_with_ts(body=body, ts=ts, sig=sig))
            assert r1.status == 202
            # Byte-identical replay (same ts + body + sig) — captured request.
            r2 = await receiver._handle_request(
                _request_with_ts(body=body, ts=ts, sig=sig, remote="9.9.9.9")
            )
            assert r2.status == 200
            decoded = json.loads(r2.text)
            assert decoded.get("deduplicated") is True
            # The side-effecting job is created EXACTLY ONCE.
            assert scheduler.create_job.call_count == 1
        finally:
            await db.close()

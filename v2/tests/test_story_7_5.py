"""Story 7.5 — TokenBucket, WebhookSettings, WebhookReceiver request handling.

Command, frequency-cap, and migration tests live in
:mod:`tests.test_story_7_5b` to keep each file under the B2 300-line cap.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from stackowl.config.webhook_settings import WebhookSettings, WebhookSourceConfig
from stackowl.webhooks.handler_job import WebhookHandlerJob
from stackowl.webhooks.rate_limit import TokenBucket
from stackowl.webhooks.receiver import WebhookEvent, WebhookReceiver
from tests._story_7_5_helpers import (
    disable_guard,
    make_mock_request,
    make_mock_scheduler,
    make_settings_with_webhooks,
    make_signature,
    open_db,
)


# ---------------------------------------------------------------------------
# 1-3. TokenBucket
# ---------------------------------------------------------------------------


def test_token_bucket_allows_up_to_max_tokens() -> None:
    bucket = TokenBucket(max_tokens=60, window_seconds=60)
    allowed = sum(1 for _ in range(60) if bucket.consume("k1"))
    assert allowed == 60


def test_token_bucket_rejects_after_limit() -> None:
    bucket = TokenBucket(max_tokens=60, window_seconds=60)
    for _ in range(60):
        assert bucket.consume("k1") is True
    assert bucket.consume("k1") is False
    assert bucket.consume("k1") is False


def test_token_bucket_uses_separate_buckets_per_key() -> None:
    bucket = TokenBucket(max_tokens=2, window_seconds=60)
    assert bucket.consume("a") is True
    assert bucket.consume("a") is True
    assert bucket.consume("a") is False  # 'a' exhausted
    # 'b' should be untouched
    assert bucket.consume("b") is True
    assert bucket.consume("b") is True
    assert bucket.consume("b") is False


def test_token_bucket_rejects_invalid_args() -> None:
    with pytest.raises(ValueError):
        TokenBucket(max_tokens=0)
    with pytest.raises(ValueError):
        TokenBucket(window_seconds=0)


# ---------------------------------------------------------------------------
# 4-5. WebhookSettings + WebhookEvent
# ---------------------------------------------------------------------------


def test_webhook_settings_defaults() -> None:
    s = WebhookSettings()
    assert s.enabled is False
    assert s.bind_address == "127.0.0.1"
    assert s.port == 8766
    assert s.sources == {}


def test_webhook_source_config_is_frozen() -> None:
    cfg = WebhookSourceConfig(secret="env:FOO")
    assert cfg.enabled is True
    with pytest.raises(ValidationError):
        cfg.secret = "env:OTHER"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        WebhookSourceConfig(secret="env:FOO", unknown_field="x")  # type: ignore[call-arg]


def test_webhook_event_is_frozen_and_forbids_extras() -> None:
    e = WebhookEvent(
        event_id="abc",
        source="src",
        payload={"k": "v"},
        received_at="2026-05-23T00:00:00+00:00",
    )
    with pytest.raises(ValidationError):
        e.event_id = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        WebhookEvent(  # type: ignore[call-arg]
            event_id="abc",
            source="src",
            payload={},
            received_at="2026-05-23T00:00:00+00:00",
            extra="x",
        )


# ---------------------------------------------------------------------------
# 6-7. Signature validation (constant-time HMAC)
# ---------------------------------------------------------------------------


def _new_receiver() -> WebhookReceiver:
    return WebhookReceiver(
        scheduler=make_mock_scheduler(), settings=make_settings_with_webhooks()
    )


async def test_validate_signature_accepts_valid_hmac() -> None:
    receiver = _new_receiver()
    body = b'{"id":"evt_1"}'
    expected = make_signature("s3cret", body)
    assert await receiver._validate_signature("s3cret", body, expected) is True


async def test_validate_signature_rejects_tampered_or_empty() -> None:
    receiver = _new_receiver()
    body = b'{"id":"evt_1"}'
    good = make_signature("s3cret", body)
    bad = "0" * len(good)
    assert await receiver._validate_signature("s3cret", body, bad) is False
    assert await receiver._validate_signature("wrong-secret", body, good) is False
    assert await receiver._validate_signature("s3cret", body, "") is False


async def test_validate_signature_strips_sha256_prefix() -> None:
    receiver = _new_receiver()
    body = b'{"x":1}'
    expected = make_signature("abc", body)
    assert await receiver._validate_signature("abc", body, f"sha256={expected}") is True


# ---------------------------------------------------------------------------
# 8-12. _handle_request — full request flow with mocked aiohttp Request
# ---------------------------------------------------------------------------


async def test_handle_request_returns_404_for_unknown_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disable_guard()
    monkeypatch.setenv("WEBHOOK_TEST_SECRET", "shared")
    settings = make_settings_with_webhooks()
    scheduler = make_mock_scheduler()
    receiver = WebhookReceiver(scheduler=scheduler, settings=settings)
    req = make_mock_request(source="unknown", body=b"{}", signature="x")
    resp = await receiver._handle_request(req)
    assert resp.status == 404


async def test_handle_request_returns_400_for_invalid_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disable_guard()
    monkeypatch.setenv("WEBHOOK_TEST_SECRET", "shared")
    settings = make_settings_with_webhooks()
    scheduler = make_mock_scheduler()
    receiver = WebhookReceiver(scheduler=scheduler, settings=settings)
    req = make_mock_request(
        source="test_source", body=b'{"a":1}', signature="bad-signature"
    )
    resp = await receiver._handle_request(req)
    assert resp.status == 400


async def test_handle_request_returns_429_when_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disable_guard()
    monkeypatch.setenv("WEBHOOK_TEST_SECRET", "shared")
    settings = make_settings_with_webhooks()
    scheduler = make_mock_scheduler()
    bucket = TokenBucket(max_tokens=1, window_seconds=60)
    receiver = WebhookReceiver(
        scheduler=scheduler, settings=settings, rate_limiter=bucket
    )
    body = b'{"x":1}'
    sig = make_signature("shared", body)
    # First request consumes the only token
    req1 = make_mock_request(source="test_source", body=body, signature=sig)
    resp1 = await receiver._handle_request(req1)
    assert resp1.status == 202
    # Second from same remote/source → rate-limited
    req2 = make_mock_request(source="test_source", body=body, signature=sig)
    resp2 = await receiver._handle_request(req2)
    assert resp2.status == 429


async def test_handle_request_enqueues_job_on_valid_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disable_guard()
    monkeypatch.setenv("WEBHOOK_TEST_SECRET", "shared")
    settings = make_settings_with_webhooks()
    scheduler = make_mock_scheduler()
    receiver = WebhookReceiver(scheduler=scheduler, settings=settings)
    body = b'{"hello":"world"}'
    sig = make_signature("shared", body)
    req = make_mock_request(source="test_source", body=body, signature=sig)
    resp = await receiver._handle_request(req)
    assert resp.status == 202
    scheduler.create_job.assert_called_once()
    call_kwargs = scheduler.create_job.call_args.kwargs
    assert call_kwargs["handler_name"] == "webhook_handler"
    assert call_kwargs["schedule"] == "@once"
    assert call_kwargs["params"]["event"]["source"] == "test_source"
    assert call_kwargs["params"]["event"]["payload"] == {"hello": "world"}


async def test_handle_request_returns_202_with_event_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    disable_guard()
    monkeypatch.setenv("WEBHOOK_TEST_SECRET", "shared")
    settings = make_settings_with_webhooks()
    scheduler = make_mock_scheduler()
    receiver = WebhookReceiver(scheduler=scheduler, settings=settings)
    body = b'{"k":"v"}'
    sig = make_signature("shared", body)
    req = make_mock_request(source="test_source", body=body, signature=sig)
    resp = await receiver._handle_request(req)
    assert resp.status == 202
    decoded = json.loads(resp.text)
    assert "event_id" in decoded
    assert isinstance(decoded["event_id"], str)
    assert len(decoded["event_id"]) == 32  # uuid4 hex


async def test_handle_request_returns_400_for_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disable_guard()
    monkeypatch.setenv("WEBHOOK_TEST_SECRET", "shared")
    settings = make_settings_with_webhooks()
    scheduler = make_mock_scheduler()
    receiver = WebhookReceiver(scheduler=scheduler, settings=settings)
    body = b"not-json"
    sig = make_signature("shared", body)
    req = make_mock_request(source="test_source", body=body, signature=sig)
    resp = await receiver._handle_request(req)
    assert resp.status == 400


async def test_handle_request_returns_404_for_disabled_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disabled source is rejected before any signature work happens."""
    disable_guard()
    monkeypatch.setenv("WEBHOOK_TEST_SECRET", "shared")
    settings = make_settings_with_webhooks(enabled=False)
    scheduler = make_mock_scheduler()
    receiver = WebhookReceiver(scheduler=scheduler, settings=settings)
    body = b"{}"
    sig = make_signature("shared", body)
    req = make_mock_request(source="test_source", body=body, signature=sig)
    resp = await receiver._handle_request(req)
    assert resp.status == 404


# ---------------------------------------------------------------------------
# 13. WebhookHandlerJob registration + handler_name
# ---------------------------------------------------------------------------


def test_webhook_handler_job_has_correct_handler_name() -> None:
    handler = WebhookHandlerJob()
    assert handler.handler_name == "webhook_handler"


# ---------------------------------------------------------------------------
# Receiver health probe — bound state
# ---------------------------------------------------------------------------


async def test_receiver_health_reports_down_when_not_bound(
    tmp_path: Path,
) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        settings = make_settings_with_webhooks()
        scheduler = make_mock_scheduler()
        receiver = WebhookReceiver(scheduler=scheduler, settings=settings, db=db)
        report = await receiver.health()
        assert report.status == "down"
        assert report.details["bound"] is False
    finally:
        await db.close()

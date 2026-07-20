"""Tests for CallbackRouter, CallbackIdempotencyStore, and MemoryCallbackHandler.

Covers:
1.  CallbackIdempotencyStore.is_processed returns False when not in table
2.  CallbackIdempotencyStore.mark_processed inserts record
3.  CallbackIdempotencyStore.is_processed returns True after mark_processed
4.  CallbackRouter.route acknowledges duplicate callback (idempotency)
5.  CallbackRouter.route calls handler for matching prefix
6.  CallbackRouter.route logs warning for unknown prefix
7.  MemoryCallbackHandler.handle_approve calls memory_bridge operation
8.  MemoryCallbackHandler.handle_reject calls memory_bridge.delete
9.  MemoryCallbackHandler.register registers both prefixes
10. CallbackRouter.route calls adapter.acknowledge_callback after handler
"""

from __future__ import annotations

import types
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stackowl.channels.telegram.callbacks import CallbackIdempotencyStore, CallbackRouter
from stackowl.channels.telegram.memory_callbacks import MemoryCallbackHandler
from stackowl.db.pool import DbPool
from stackowl.memory.bridge import NullMemoryBridge


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    """Provide a fresh in-memory DbPool for each test."""
    pool = DbPool(db_path=tmp_path / "test_callbacks.db")
    await pool.open()
    yield pool
    await pool.close()


def _make_callback_update(
    callback_id: str, callback_data: str, from_user_id: int | None = None
) -> Any:
    """Build a duck-typed Update object with a callback_query.

    ``from_user_id`` is omitted by default (no ``from_user`` attribute at
    all) to exercise the router's fall-back-to-None extraction path; pass it
    to exercise the successful-extraction path.
    """
    if from_user_id is None:
        cq = types.SimpleNamespace(id=callback_id, data=callback_data)
    else:
        from_user = types.SimpleNamespace(id=from_user_id)
        cq = types.SimpleNamespace(id=callback_id, data=callback_data, from_user=from_user)
    return types.SimpleNamespace(callback_query=cq)


def _make_adapter() -> Any:
    """Return a mock adapter with acknowledge_callback stubbed."""
    from stackowl.channels.telegram.settings import TelegramSettings
    from stackowl.channels.telegram.adapter import TelegramChannelAdapter

    settings = TelegramSettings(
        bot_token="test_token_x" * 3,
        allowed_user_ids=frozenset({42}),
    )
    adapter = TelegramChannelAdapter(settings)
    adapter.acknowledge_callback = AsyncMock()
    return adapter


# ---------------------------------------------------------------------------
# 1. CallbackIdempotencyStore.is_processed returns False when not in table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_processed_false_when_not_in_table(db_pool: DbPool) -> None:
    store = CallbackIdempotencyStore(db_pool)
    await store.ensure_table()
    result = await store.is_processed("nonexistent-id")
    assert result is False


# ---------------------------------------------------------------------------
# 2. CallbackIdempotencyStore.mark_processed inserts record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_processed_inserts_record(db_pool: DbPool) -> None:
    store = CallbackIdempotencyStore(db_pool)
    await store.ensure_table()
    await store.mark_processed("cb-001", "some:data")

    rows = await db_pool.fetch_all(
        "SELECT callback_id, callback_data FROM callback_log WHERE callback_id = ?",
        ("cb-001",),
    )
    assert len(rows) == 1
    assert rows[0]["callback_id"] == "cb-001"
    assert rows[0]["callback_data"] == "some:data"


# ---------------------------------------------------------------------------
# 3. CallbackIdempotencyStore.is_processed returns True after mark_processed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_processed_true_after_mark(db_pool: DbPool) -> None:
    store = CallbackIdempotencyStore(db_pool)
    await store.ensure_table()
    await store.mark_processed("cb-002", "action:something")
    result = await store.is_processed("cb-002")
    assert result is True


# ---------------------------------------------------------------------------
# 4. CallbackRouter.route acknowledges duplicate callback (idempotency)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_acknowledges_duplicate_without_calling_handler(db_pool: DbPool) -> None:
    adapter = _make_adapter()
    router = CallbackRouter(db_pool=db_pool, adapter=adapter)

    # Pre-seed idempotency table
    await router._store.ensure_table()
    await router._store.mark_processed("dup-cb", "action:x")

    handler = AsyncMock()
    router.register("action:", handler)

    update = _make_callback_update("dup-cb", "action:x")
    await router.route(update, None)

    # Duplicate: handler NOT called, but adapter.acknowledge_callback IS called
    handler.assert_not_called()
    adapter.acknowledge_callback.assert_called_once_with("dup-cb")


# ---------------------------------------------------------------------------
# 5. CallbackRouter.route calls handler for matching prefix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_calls_handler_for_matching_prefix(db_pool: DbPool) -> None:
    adapter = _make_adapter()
    router = CallbackRouter(db_pool=db_pool, adapter=adapter)
    await router._store.ensure_table()

    handler = AsyncMock()
    router.register("mem:approve:", handler)

    update = _make_callback_update("cb-fresh", "mem:approve:fact123")
    await router.route(update, None)

    # Duck-typed update's callback_query carries no from_user, so chat_id
    # extraction falls back to None — still passed through positionally.
    handler.assert_called_once_with("cb-fresh", "mem:approve:fact123", None)


# ---------------------------------------------------------------------------
# 5b. CallbackRouter.route extracts chat_id from callback_query.from_user.id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_extracts_chat_id_from_callback_query(db_pool: DbPool) -> None:
    adapter = _make_adapter()
    router = CallbackRouter(db_pool=db_pool, adapter=adapter)
    await router._store.ensure_table()

    handler = AsyncMock()
    router.register("mem:approve:", handler)

    update = _make_callback_update("cb-with-user", "mem:approve:fact456", from_user_id=98765)
    await router.route(update, None)

    handler.assert_called_once_with("cb-with-user", "mem:approve:fact456", 98765)


# ---------------------------------------------------------------------------
# 5c. CallbackRouter.route never raises when from_user is present but malformed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_falls_back_to_none_when_from_user_has_no_id(db_pool: DbPool) -> None:
    """A ``from_user`` object present but missing/None ``id`` must never crash
    chat_id extraction — falls back to None (see callbacks.py route())."""
    adapter = _make_adapter()
    router = CallbackRouter(db_pool=db_pool, adapter=adapter)
    await router._store.ensure_table()

    handler = AsyncMock()
    router.register("mem:approve:", handler)

    cq = types.SimpleNamespace(
        id="cb-malformed", data="mem:approve:fact789", from_user=object()
    )
    update = types.SimpleNamespace(callback_query=cq)
    await router.route(update, None)

    handler.assert_called_once_with("cb-malformed", "mem:approve:fact789", None)


# ---------------------------------------------------------------------------
# 6. CallbackRouter.route logs warning for unknown prefix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_logs_warning_for_unknown_prefix(db_pool: DbPool) -> None:
    adapter = _make_adapter()
    router = CallbackRouter(db_pool=db_pool, adapter=adapter)
    await router._store.ensure_table()

    update = _make_callback_update("cb-unk", "unknown:data")

    with patch("stackowl.channels.telegram.callbacks.log") as mock_log:
        await router.route(update, None)
        mock_log.telegram.warning.assert_called()


# ---------------------------------------------------------------------------
# 7. MemoryCallbackHandler.handle_approve calls memory_bridge operation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_approve_calls_bridge_operation() -> None:
    bridge = MagicMock(spec=NullMemoryBridge)
    bridge.stage = AsyncMock()
    bridge.delete = AsyncMock()
    # No force_promote method on NullMemoryBridge — will use stage fallback

    adapter = _make_adapter()

    handler = MemoryCallbackHandler(memory_bridge=bridge, adapter=adapter)
    await handler.handle_approve("cb-approve", "mem:approve:fact-XYZ")

    # The bridge must have been interacted with (stage or force_promote)
    assert bridge.stage.called or getattr(bridge, "force_promote", None)
    adapter.acknowledge_callback.assert_called_once()


# ---------------------------------------------------------------------------
# 8. MemoryCallbackHandler.handle_reject calls memory_bridge.delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_reject_calls_bridge_delete() -> None:
    bridge = MagicMock(spec=NullMemoryBridge)
    bridge.delete = AsyncMock()

    adapter = _make_adapter()

    handler = MemoryCallbackHandler(memory_bridge=bridge, adapter=adapter)
    await handler.handle_reject("cb-reject", "mem:reject:fact-ABC")

    bridge.delete.assert_called_once_with("fact-ABC")
    adapter.acknowledge_callback.assert_called_once()


# ---------------------------------------------------------------------------
# 9. MemoryCallbackHandler.register registers both prefixes
# ---------------------------------------------------------------------------


def test_register_attaches_both_prefixes() -> None:
    bridge = NullMemoryBridge()
    adapter = _make_adapter()
    handler = MemoryCallbackHandler(memory_bridge=bridge, adapter=adapter)

    router_mock = MagicMock()
    handler.register(router_mock)

    calls = [call.args[0] for call in router_mock.register.call_args_list]
    assert "mem:approve:" in calls
    assert "mem:reject:" in calls


# ---------------------------------------------------------------------------
# 10. CallbackRouter.route calls adapter.acknowledge_callback after handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_acknowledges_after_handler(db_pool: DbPool) -> None:
    adapter = _make_adapter()
    router = CallbackRouter(db_pool=db_pool, adapter=adapter)
    await router._store.ensure_table()

    handler = AsyncMock()
    router.register("test:", handler)

    update = _make_callback_update("cb-ack", "test:payload")
    await router.route(update, None)

    handler.assert_called_once()
    adapter.acknowledge_callback.assert_called_once_with("cb-ack")

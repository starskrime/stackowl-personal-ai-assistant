"""Trust-stamp tests for the Telegram human-confirm (approve) path.

Asserts that when a human confirms a fact via Telegram (the fallback StagedFact
construction path — always taken because MemoryBridge has no force_promote), the
resulting StagedFact carries trust="trusted" (per memory.trust: "manual" -> "trusted").

See also: channels/telegram/memory_callbacks.py handle_approve, memory/trust.py.

FR: memory-gov E — telegram human-confirm fact must be stamped trusted.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.channels.telegram.memory_callbacks import MemoryCallbackHandler
from stackowl.memory.bridge import NullMemoryBridge
from stackowl.memory.models import StagedFact

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter() -> Any:
    """Return a minimal mock adapter with acknowledge_callback stubbed."""
    from stackowl.channels.telegram.adapter import TelegramChannelAdapter
    from stackowl.channels.telegram.settings import TelegramSettings

    settings = TelegramSettings(
        bot_token="test_trust_token_xyz" * 2,
        allowed_user_ids=frozenset({99}),
    )
    adapter = TelegramChannelAdapter(settings)
    adapter.acknowledge_callback = AsyncMock()
    return adapter


# ---------------------------------------------------------------------------
# Trust stamp tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_approve_fallback_stages_trusted_fact() -> None:
    """The fallback StagedFact constructed in handle_approve must carry trust='trusted'.

    MemoryBridge has no force_promote attribute, so the hasattr guard is always False
    and the else-branch (new StagedFact) always runs. This test verifies that the
    StagedFact passed to bridge.stage() has trust='trusted'.
    """
    bridge = MagicMock(spec=NullMemoryBridge)
    bridge.stage = AsyncMock()
    # Explicitly ensure force_promote is NOT present (mirrors NullMemoryBridge reality)
    assert not hasattr(bridge, "force_promote"), (
        "Test assumption violated: NullMemoryBridge should not have force_promote"
    )

    adapter = _make_adapter()
    handler = MemoryCallbackHandler(memory_bridge=bridge, adapter=adapter)

    await handler.handle_approve("cb-trust-001", "mem:approve:fact-trust-test")

    bridge.stage.assert_called_once()
    staged: StagedFact = bridge.stage.call_args[0][0]
    assert isinstance(staged, StagedFact), f"Expected StagedFact, got {type(staged)}"
    assert staged.trust == "trusted", (
        f"Human-confirmed fact must be trusted, got trust={staged.trust!r}"
    )


@pytest.mark.asyncio
async def test_handle_approve_fallback_staged_fact_source_type_is_manual() -> None:
    """The fallback StagedFact must have source_type='manual' (confirms path identity)."""
    bridge = MagicMock(spec=NullMemoryBridge)
    bridge.stage = AsyncMock()

    adapter = _make_adapter()
    handler = MemoryCallbackHandler(memory_bridge=bridge, adapter=adapter)

    await handler.handle_approve("cb-src-001", "mem:approve:fact-src-test")

    staged: StagedFact = bridge.stage.call_args[0][0]
    assert staged.source_type == "manual", (
        f"Expected source_type='manual', got {staged.source_type!r}"
    )


@pytest.mark.asyncio
async def test_handle_approve_with_force_promote_does_not_construct_new_fact() -> None:
    """When force_promote IS present on the bridge, the else-branch must NOT run.

    This is not the real-world path (MemoryBridge never has force_promote), but
    ensures the fallback guard logic is correct so a future wiring fix will work.
    """
    bridge = MagicMock(spec=NullMemoryBridge)
    bridge.stage = AsyncMock()
    bridge.force_promote = AsyncMock(return_value=True)  # inject the attribute

    adapter = _make_adapter()
    handler = MemoryCallbackHandler(memory_bridge=bridge, adapter=adapter)

    await handler.handle_approve("cb-fp-001", "mem:approve:fact-fp-test")

    # force_promote path was taken — stage must NOT have been called
    bridge.force_promote.assert_called_once_with("fact-fp-test")
    bridge.stage.assert_not_called()

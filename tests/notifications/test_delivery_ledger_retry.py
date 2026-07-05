"""DeliveryLedger.claim_dispatch — a FAILED occurrence must be re-claimable.

Migration 0055's documented intent is "a 'failed' row permits an honest retry next
run", but the original ``ON CONFLICT DO NOTHING`` made every prior row (including a
failed one) suppress forever — so one transient send failure permanently burned the
occurrence. These guard the corrected semantics without breaking exactly-once for
genuinely ``delivered`` / in-flight ``dispatched`` rows.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.notifications.delivery_ledger import DeliveryLedger

pytestmark = pytest.mark.asyncio


async def test_failed_occurrence_is_reclaimable(tmp_db: DbPool) -> None:
    ledger = DeliveryLedger(tmp_db)
    assert await ledger.claim_dispatch("j1", "occ1", "telegram") is True
    await ledger.mark("j1", "occ1", "telegram", "failed")
    # A later run for the SAME occurrence+channel must be allowed to retry.
    assert await ledger.claim_dispatch("j1", "occ1", "telegram") is True


async def test_delivered_occurrence_stays_locked(tmp_db: DbPool) -> None:
    ledger = DeliveryLedger(tmp_db)
    assert await ledger.claim_dispatch("j1", "occ1", "telegram") is True
    await ledger.mark("j1", "occ1", "telegram", "delivered")
    # Exactly-once: a delivered occurrence never re-sends.
    assert await ledger.claim_dispatch("j1", "occ1", "telegram") is False


async def test_inflight_dispatched_occurrence_is_suppressed(tmp_db: DbPool) -> None:
    ledger = DeliveryLedger(tmp_db)
    assert await ledger.claim_dispatch("j1", "occ1", "telegram") is True
    # Still 'dispatched' (in-flight, not yet marked) — a concurrent claim must lose.
    assert await ledger.claim_dispatch("j1", "occ1", "telegram") is False


async def test_reclaimed_failed_row_can_be_marked_delivered(tmp_db: DbPool) -> None:
    """A re-claim followed by a successful send locks the occurrence for good."""
    ledger = DeliveryLedger(tmp_db)
    await ledger.claim_dispatch("j1", "occ1", "telegram")
    await ledger.mark("j1", "occ1", "telegram", "failed")
    assert await ledger.claim_dispatch("j1", "occ1", "telegram") is True
    await ledger.mark("j1", "occ1", "telegram", "delivered")
    assert await ledger.claim_dispatch("j1", "occ1", "telegram") is False

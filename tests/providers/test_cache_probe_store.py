"""D01.2 — the probe learns ASYMMETRICALLY, and that asymmetry is load-bearing.

Only POSITIVE results are durable. A zero is never written and never treated as
"the markers are dead", because a zero is ambiguous by construction: a
below-minimum marker, a cold cache, and a gateway that strips usage fields all
read zero (D01.6's invariant I4).

The failure this prevents is concrete. Bakir first chose "trust cache_creation on
the first response" AND "persist it". Those two interact badly: a field-stripping
gateway guarantees a zero on turn one, which would be persisted as "marker is
dead" and disable the feature FOREVER, across restarts, with no error. D01.6
already measured that NeraAiRaw reports no cache fields in any accepted shape.
Positives-only self-heals: the worst case is that we keep trying and waste
nothing.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.providers.cache_probe_store import CacheProbeStore

pytestmark = pytest.mark.asyncio


async def test_a_positive_reading_is_persisted(tmp_db: DbPool) -> None:
    store = CacheProbeStore(tmp_db)
    await store.record(
        provider_name="anthropic-main", model="claude-opus-5",
        markers_placed=4, cache_creation_tokens=2712, cache_read_tokens=0,
    )
    probe = await store.load(provider_name="anthropic-main", model="claude-opus-5")
    assert probe is not None
    assert probe.cache_creation_tokens == 2712
    assert probe.markers_placed == 4


async def test_a_zero_reading_is_never_persisted(tmp_db: DbPool) -> None:
    """I5 — the invariant the whole design hangs on."""
    store = CacheProbeStore(tmp_db)
    await store.record(
        provider_name="anthropic-main", model="claude-opus-5",
        markers_placed=4, cache_creation_tokens=0, cache_read_tokens=0,
    )
    assert await store.load(provider_name="anthropic-main", model="claude-opus-5") is None


async def test_a_zero_after_a_positive_never_erases_the_positive(tmp_db: DbPool) -> None:
    """I5's sharper half: confirmation is DURABLE.

    A cold turn after a confirmed one reads zero. If that overwrote the
    confirmation, the endpoint would flip between "known good" and "unknown" every
    conversation and the knowledge would never accumulate.
    """
    store = CacheProbeStore(tmp_db)
    await store.record(
        provider_name="anthropic-main", model="claude-opus-5",
        markers_placed=4, cache_creation_tokens=2712, cache_read_tokens=0,
    )
    await store.record(
        provider_name="anthropic-main", model="claude-opus-5",
        markers_placed=4, cache_creation_tokens=0, cache_read_tokens=0,
    )
    probe = await store.load(provider_name="anthropic-main", model="claude-opus-5")
    assert probe is not None
    assert probe.cache_creation_tokens == 2712


async def test_a_cache_read_alone_counts_as_confirmation(tmp_db: DbPool) -> None:
    """Turn 2 of a conversation READS without creating. That is still proof the
    endpoint honours markers — arguably better proof, since a read can only happen
    if an earlier write was honoured."""
    store = CacheProbeStore(tmp_db)
    await store.record(
        provider_name="anthropic-main", model="claude-opus-5",
        markers_placed=4, cache_creation_tokens=0, cache_read_tokens=2712,
    )
    probe = await store.load(provider_name="anthropic-main", model="claude-opus-5")
    assert probe is not None
    assert probe.cache_read_tokens == 2712


async def test_the_first_confirmation_time_is_never_overwritten(tmp_db: DbPool) -> None:
    store = CacheProbeStore(tmp_db)
    await store.record(
        provider_name="anthropic-main", model="claude-opus-5",
        markers_placed=4, cache_creation_tokens=100, cache_read_tokens=0,
        now="2026-01-01T00:00:00+00:00",
    )
    await store.record(
        provider_name="anthropic-main", model="claude-opus-5",
        markers_placed=4, cache_creation_tokens=200, cache_read_tokens=50,
        now="2026-06-06T00:00:00+00:00",
    )
    probe = await store.load(provider_name="anthropic-main", model="claude-opus-5")
    assert probe is not None
    assert probe.first_confirmed_at == "2026-01-01T00:00:00+00:00"
    assert probe.last_confirmed_at == "2026-06-06T00:00:00+00:00"


async def test_the_largest_reading_is_kept_not_the_latest(tmp_db: DbPool) -> None:
    """The question this table answers is "what has this endpoint been SEEN to
    do", so the high-water mark is the honest answer — a later smaller reading is
    a smaller prompt, not a degraded endpoint."""
    store = CacheProbeStore(tmp_db)
    await store.record(
        provider_name="anthropic-main", model="claude-opus-5",
        markers_placed=4, cache_creation_tokens=5000, cache_read_tokens=0,
    )
    await store.record(
        provider_name="anthropic-main", model="claude-opus-5",
        markers_placed=4, cache_creation_tokens=12, cache_read_tokens=0,
    )
    probe = await store.load(provider_name="anthropic-main", model="claude-opus-5")
    assert probe is not None
    assert probe.cache_creation_tokens == 5000


async def test_two_models_on_one_provider_are_tracked_separately(tmp_db: DbPool) -> None:
    """The minimum cacheable prefix is MODEL-dependent and not monotonic, so one
    model confirming says nothing about its sibling."""
    store = CacheProbeStore(tmp_db)
    await store.record(
        provider_name="anthropic-main", model="claude-opus-5",
        markers_placed=4, cache_creation_tokens=2712, cache_read_tokens=0,
    )
    assert await store.load(
        provider_name="anthropic-main", model="claude-haiku-4-5-20251001"
    ) is None


async def test_an_unknown_endpoint_reads_none_rather_than_raising(tmp_db: DbPool) -> None:
    store = CacheProbeStore(tmp_db)
    assert await store.load(provider_name="nope", model="nope") is None


async def test_a_write_failure_never_raises(tmp_db: DbPool) -> None:
    """Fail-open, like every other measurement seam: losing a probe record must
    cost knowledge, never a turn."""
    await tmp_db.execute("DROP TABLE cache_breakpoint_probes")
    store = CacheProbeStore(tmp_db)
    await store.record(
        provider_name="anthropic-main", model="claude-opus-5",
        markers_placed=4, cache_creation_tokens=2712, cache_read_tokens=0,
    )  # must not raise


async def test_a_read_failure_never_raises(tmp_db: DbPool) -> None:
    await tmp_db.execute("DROP TABLE cache_breakpoint_probes")
    store = CacheProbeStore(tmp_db)
    assert await store.load(provider_name="anthropic-main", model="claude-opus-5") is None

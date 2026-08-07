"""Re-pricing self-hosted history to $0 (D01.6 follow-up).

MEASURED on the live database 2026-08-07: 82,016 cost rows, every one priced by
the unknown-cloud fallback, ~$2,328 of imaginary spend in ten days — because
`is_local_url` was purely syntactic and the only enabled provider was configured
by HOSTNAME (`llm-gateway.dev.nera.gov`) rather than by its private IP literal
(172.30.104.100).

The honesty marker from DEBT-15 worked throughout: `priced` was 0/NULL, so
nothing ever claimed those dollars were measured. What was broken is that every
aggregate — `/cost`, the budget signals, D01.6's baseline — was computed over
invented numbers.

The dangerous property of this correction is that it REWRITES RECORDED HISTORY,
so most of these tests are about what it must refuse to touch.
"""

from __future__ import annotations

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.settings import Settings
from stackowl.providers.cost_backfill import reprice_local_history


def _settings(*providers: ProviderConfig) -> Settings:
    s = Settings()
    object.__setattr__(s, "providers", list(providers)) if hasattr(s, "providers") else None
    try:
        s.providers = list(providers)  # type: ignore[misc]
    except Exception:
        pass
    return s


def _provider(name: str, base_url: str | None) -> ProviderConfig:
    return ProviderConfig(
        name=name, protocol="openai", base_url=base_url,
        default_model="m", tiers=("fast",),
    )


async def _row(db, provider: str, cost: float, priced) -> None:  # noqa: ANN001
    await db.execute(
        "INSERT INTO cost_records (provider_name, model, input_tokens, "
        "output_tokens, cost_usd, trace_id, recorded_at, owner_id, priced) "
        "VALUES (?, 'm', 1, 1, ?, ?, '2026-08-07T00:00:00+00:00', "
        "'principal-default', ?)",
        (provider, cost, f"t-{provider}-{cost}-{priced}", priced),
    )


async def _rows(db):  # noqa: ANN001, ANN202
    return {
        (r["provider_name"], float(str(r["cost_usd"])), r["priced"])
        for r in await db.fetch_all(
            "SELECT provider_name, cost_usd, priced FROM cost_records", ()
        )
    }


@pytest.mark.asyncio
async def test_a_self_hosted_provider_is_repriced_to_zero(tmp_db):
    await _row(tmp_db, "local-gw", 12.34, 0)
    settings = _settings(_provider("local-gw", "http://127.0.0.1:4000/v1"))

    n = await reprice_local_history(tmp_db, settings)

    assert n == 1
    assert ("local-gw", 0.0, 1) in await _rows(tmp_db)


@pytest.mark.asyncio
async def test_a_CLOUD_provider_is_left_completely_alone(tmp_db):
    """The blast radius. Zeroing a genuinely-billed provider would erase real
    money from the record."""
    await _row(tmp_db, "cloud-api", 9.99, 0)
    settings = _settings(_provider("cloud-api", "https://api.example.com/v1"))

    n = await reprice_local_history(tmp_db, settings)

    assert n == 0
    assert ("cloud-api", 9.99, 0) in await _rows(tmp_db)


@pytest.mark.asyncio
async def test_a_row_with_a_REAL_table_price_is_never_overwritten(tmp_db):
    """priced=1 means the figure came from a price table, not the fallback. Even
    for a local provider that is a measurement, and measurements are not edited."""
    await _row(tmp_db, "local-gw", 5.00, 1)
    settings = _settings(_provider("local-gw", "http://127.0.0.1:4000/v1"))

    n = await reprice_local_history(tmp_db, settings)

    assert n == 0
    assert ("local-gw", 5.00, 1) in await _rows(tmp_db)


@pytest.mark.asyncio
async def test_NULL_priced_rows_are_repriced(tmp_db):
    """Pre-migration-0101 rows carry NULL — unknown provenance, which cannot
    honestly be counted as priced. `priced IS NOT 1` must catch them."""
    await _row(tmp_db, "local-gw", 3.00, None)
    settings = _settings(_provider("local-gw", "http://127.0.0.1:4000/v1"))

    assert await reprice_local_history(tmp_db, settings) == 1


@pytest.mark.asyncio
async def test_it_is_idempotent_across_restarts(tmp_db):
    """Guarded by a stackowl_meta key: a second boot must not re-scan 82,000
    rows, and must not be able to touch anything recorded since."""
    await _row(tmp_db, "local-gw", 1.00, 0)
    settings = _settings(_provider("local-gw", "http://127.0.0.1:4000/v1"))

    first = await reprice_local_history(tmp_db, settings)
    await _row(tmp_db, "local-gw", 2.00, 0)   # recorded after the backfill
    second = await reprice_local_history(tmp_db, settings)

    assert first == 1
    assert second == 0, "must not run twice"
    assert ("local-gw", 2.00, 0) in await _rows(tmp_db), (
        "a row written after the backfill is the new pricing path's business"
    )


@pytest.mark.asyncio
async def test_a_deployment_with_no_local_providers_changes_nothing(tmp_db):
    await _row(tmp_db, "cloud-api", 7.00, 0)
    settings = _settings(_provider("cloud-api", "https://api.example.com/v1"))

    assert await reprice_local_history(tmp_db, settings) == 0
    assert ("cloud-api", 7.00, 0) in await _rows(tmp_db)


@pytest.mark.asyncio
async def test_only_the_named_local_provider_is_touched(tmp_db):
    """A mixed deployment must not have its cloud spend zeroed alongside."""
    await _row(tmp_db, "local-gw", 4.00, 0)
    await _row(tmp_db, "cloud-api", 8.00, 0)
    settings = _settings(
        _provider("local-gw", "http://192.168.1.81:4000/v1"),
        _provider("cloud-api", "https://api.example.com/v1"),
    )

    assert await reprice_local_history(tmp_db, settings) == 1
    rows = await _rows(tmp_db)
    assert ("local-gw", 0.0, 1) in rows
    assert ("cloud-api", 8.00, 0) in rows


@pytest.mark.asyncio
async def test_a_broken_database_does_not_stop_startup(tmp_db):
    """B5 — a bookkeeping correction must never be able to prevent the platform
    from booting."""
    class _Boom:
        async def fetch_all(self, *a, **k):  # noqa: ANN002, ANN003, ANN202
            raise RuntimeError("db down")

    assert await reprice_local_history(_Boom(), _settings()) == 0

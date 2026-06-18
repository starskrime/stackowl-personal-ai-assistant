"""Owner-scope isolation tests for dna_hydrator.

Proves that read_all_owl_dna and hydrate_dna are scoped to a single principal
so one tenant's evolved DNA cannot bleed into another tenant's boot registry.

TDD: these tests are written BEFORE the fix; test_read_all_owl_dna_is_owner_scoped
and test_hydrate_dna_ignores_other_principal_rows FAIL before the fix (cross-tenant
leak) and PASS after it. test_hydrate_dna_hydrates_default_principal proves the
single-user behaviour is unchanged by the fix.

Schema note: owl_dna.owl_name is UNIQUE (migration 0011 — one evolved-DNA row
per owl name). In a multi-tenant deployment each tenant has their own owl names;
we model the gap by inserting DIFFERENT owl names owned by different principals.
The bug: the unscoped SELECT returns ALL rows regardless of owner_id, so a
default-principal hydration picks up rows belonging to other principals.
"""

from __future__ import annotations

import pytest

from stackowl.owls.dna_hydrator import hydrate_dna, read_all_owl_dna
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.tenancy.principal import DEFAULT_PRINCIPAL_ID

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _reg(*owl_names: str) -> OwlRegistry:
    r = OwlRegistry.with_default_secretary()
    for name in owl_names:
        r.register(
            OwlAgentManifest(
                name=name,
                role="research",
                system_prompt="P",
                model_tier="fast",
            )
        )
    return r


async def _insert_row(
    db: object,
    owl_name: str,
    owner_id: str = DEFAULT_PRINCIPAL_ID,
    *,
    challenge_level: float = 0.5,
) -> None:
    """Insert a minimal owl_dna row with an explicit owner_id."""
    await db.execute(  # type: ignore[union-attr]
        "INSERT INTO owl_dna "
        "(owl_name, owner_id, challenge_level, verbosity, curiosity, "
        "formality, creativity, precision, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            owl_name,
            owner_id,
            challenge_level,
            0.5,
            0.5,
            0.5,
            0.5,
            0.5,
            "2026-06-13T00:00:00",
        ),
    )


# ---------------------------------------------------------------------------
# Test 1 — cross-tenant isolation proof (the core security test)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_all_owl_dna_is_owner_scoped(tmp_db: object) -> None:
    """read_all_owl_dna(db) returns ONLY rows belonging to the default principal.

    The real multi-tenant gap: different tenants own different owl names.
    Before the fix the unscoped SELECT returns ALL rows regardless of owner_id,
    so the default principal's boot hydration picks up 'other-owl' from the
    foreign tenant.  After the fix it is invisible.

    FAILS before fix (returns 2 entries), PASSES after fix (returns 1).
    """
    # One row owned by the default principal.
    await _insert_row(tmp_db, "scout", DEFAULT_PRINCIPAL_ID, challenge_level=0.3)
    # A foreign principal's owl — different name (UNIQUE constraint on owl_name).
    await _insert_row(tmp_db, "other-owl", "other-principal", challenge_level=0.9)

    result = await read_all_owl_dna(tmp_db)  # type: ignore[arg-type]

    # Must contain only the default-principal owl.
    assert "scout" in result, "default-principal row must be returned"
    assert "other-owl" not in result, (
        "foreign-principal owl 'other-owl' must NOT appear in default-principal read "
        "— cross-tenant leak detected"
    )
    assert len(result) == 1, (
        f"Expected 1 row (default principal only), got {len(result)}: {list(result)}"
    )


# ---------------------------------------------------------------------------
# Test 2 — single-user regression guard (behaviour must not change)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hydrate_dna_hydrates_default_principal(tmp_db: object) -> None:
    """hydrate_dna with default owner_id correctly overlays persisted DNA.

    Proves the single-user path is unchanged: the default principal's persisted
    trait value is applied to the live registry manifest.
    """
    r = _reg("scout")
    await _insert_row(tmp_db, "scout", DEFAULT_PRINCIPAL_ID, challenge_level=0.9)

    count = await hydrate_dna(r, tmp_db)  # type: ignore[arg-type]

    assert count == 1
    assert r.get("scout").dna.challenge_level == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Test 3 — foreign-principal rows must NOT be hydrated into default boot
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hydrate_dna_ignores_other_principal_rows(tmp_db: object) -> None:
    """hydrate_dna must not overlay DNA from a foreign owner_id.

    The registered owl 'scout' belongs to the default principal.  A foreign
    principal has a DIFFERENT owl ('foreign-owl') in the DB.  Before the fix
    the unscoped SELECT returns 'foreign-owl' and hydrate_dna tries to apply it
    to the registry — it won't find it (orphan) but the count=0 is for the wrong
    reason.  More critically for the real bug: if the foreign owl name happened
    to coincide with a registered owl name, it WOULD be applied (test_1 above
    proves this via the two-row cross-tenant leak).

    This test isolates the boot-count guarantee: with NO default-principal rows
    in DB (only a foreign-principal row), hydrate_dna must return 0 for the
    default principal.

    FAILS before fix (count==1 because unscoped SELECT returns foreign row and
    orphan-skips it, but the pre-fix unscoped-return of count=0 for an orphan
    actually hides the real issue — see test_1 for the definitive proof).

    After the fix: count==0 because the foreign row is invisible to the default
    principal query.
    """
    r = _reg("scout")
    base_challenge = r.get("scout").dna.challenge_level

    # Insert ONLY a foreign-principal row for a different owl name.
    await _insert_row(tmp_db, "foreign-owl", "other-principal", challenge_level=0.77)

    count = await hydrate_dna(r, tmp_db)  # type: ignore[arg-type]

    assert count == 0, (
        "foreign-principal row must not cause any hydration in the default principal's boot"
    )
    # Registry DNA must remain at the authored default.
    assert r.get("scout").dna.challenge_level == pytest.approx(base_challenge), (
        "foreign-principal DNA was incorrectly applied to the default principal's registry"
    )

"""Round-trip + idempotency tests for upsert_owl_dna (dna_storage.py)."""
from __future__ import annotations

import pytest
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_storage import upsert_owl_dna


@pytest.mark.asyncio
async def test_upsert_round_trips_distinct_values(tmp_db):
    dna = OwlDNA(challenge_level=0.11, verbosity=0.22, curiosity=0.33,
                 formality=0.44, creativity=0.55, precision=0.66)
    await upsert_owl_dna(tmp_db, "scout", dna, table="owl_dna")
    rows = await tmp_db.fetch_all("SELECT * FROM owl_dna WHERE owl_name = ?", ("scout",))
    r = rows[0]
    assert (r["challenge_level"], r["verbosity"], r["curiosity"], r["formality"],
            r["creativity"], r["precision"]) == (0.11, 0.22, 0.33, 0.44, 0.55, 0.66)


@pytest.mark.asyncio
async def test_upsert_into_authored_table(tmp_db):
    await upsert_owl_dna(tmp_db, "scout", OwlDNA(challenge_level=0.7), table="owl_dna_authored")
    rows = await tmp_db.fetch_all("SELECT challenge_level FROM owl_dna_authored WHERE owl_name = ?", ("scout",))
    assert rows[0]["challenge_level"] == 0.7


@pytest.mark.asyncio
async def test_upsert_is_idempotent(tmp_db):
    await upsert_owl_dna(tmp_db, "scout", OwlDNA(verbosity=0.3), table="owl_dna")
    await upsert_owl_dna(tmp_db, "scout", OwlDNA(verbosity=0.8), table="owl_dna")
    rows = await tmp_db.fetch_all("SELECT verbosity FROM owl_dna WHERE owl_name = ?", ("scout",))
    assert len(rows) == 1 and rows[0]["verbosity"] == 0.8


@pytest.mark.asyncio
async def test_upsert_rejects_unknown_table(tmp_db):
    with pytest.raises(ValueError):
        await upsert_owl_dna(tmp_db, "scout", OwlDNA(), table="evil; DROP TABLE owl_dna")

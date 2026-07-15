"""Round-trip + owner-isolation tests for LearningArtifactStore (Story 2.1).

Covers AC #2 (checkpoint/restore round-trip, exact payload) and AC #3 (the
checkpoint row itself doubles as the audit record — no separate audit table).
"""

from __future__ import annotations

import pytest

from stackowl.exceptions import ManifestValidationError
from stackowl.owls.learning_artifact_store import LearningArtifactStore

# Mirrors owls/dna_defaults.TRAIT_NAMES — a realistic DNA-shaped payload.
_DNA_PAYLOAD: dict[str, object] = {
    "challenge_level": 0.123456789,
    "verbosity": 0.234567891,
    "curiosity": 0.345678912,
    "formality": 0.456789123,
    "creativity": 0.567891234,
    "precision": 0.678912345,
    "completion_drive": 0.789123456,
}

_SKILL_PAYLOAD: dict[str, object] = {
    "skill_name": "recall_memory",
    "success_rate": 0.91,
    "tier": "guaranteed",
    "tags": ["memory", "recall"],
}


@pytest.mark.asyncio
async def test_checkpoint_restore_round_trip_dna(tmp_db):
    store = LearningArtifactStore(tmp_db)
    checkpoint_id = await store.checkpoint("dna", "scout", _DNA_PAYLOAD, reason="evolution")
    restored = await store.restore("dna", "scout", checkpoint_id)
    assert restored == _DNA_PAYLOAD


@pytest.mark.asyncio
async def test_checkpoint_restore_round_trip_skill(tmp_db):
    store = LearningArtifactStore(tmp_db)
    checkpoint_id = await store.checkpoint("skill", "recall_memory", _SKILL_PAYLOAD)
    restored = await store.restore("skill", "recall_memory", checkpoint_id)
    assert restored == _SKILL_PAYLOAD


@pytest.mark.asyncio
async def test_restore_exact_no_float_drift(tmp_db):
    """DNA trait floats must round-trip byte-exact through json.dumps/loads."""
    store = LearningArtifactStore(tmp_db)
    checkpoint_id = await store.checkpoint("dna", "scout", _DNA_PAYLOAD)
    restored = await store.restore("dna", "scout", checkpoint_id)
    for key, value in _DNA_PAYLOAD.items():
        assert restored[key] == value
        assert isinstance(restored[key], float)


@pytest.mark.asyncio
async def test_restore_unknown_checkpoint_raises(tmp_db):
    store = LearningArtifactStore(tmp_db)
    with pytest.raises(ManifestValidationError):
        await store.restore("dna", "scout", "does-not-exist")


@pytest.mark.asyncio
async def test_list_checkpoints_ordering(tmp_db):
    store = LearningArtifactStore(tmp_db)
    first = await store.checkpoint("dna", "scout", {"challenge_level": 0.1}, reason="first")
    second = await store.checkpoint("dna", "scout", {"challenge_level": 0.2}, reason="second")
    checkpoints = await store.list_checkpoints("dna", "scout")
    ids = [row["checkpoint_id"] for row in checkpoints]
    # Newest first.
    assert ids.index(second) < ids.index(first)


@pytest.mark.asyncio
async def test_list_checkpoints_limit_coercion(tmp_db):
    store = LearningArtifactStore(tmp_db)
    for i in range(3):
        await store.checkpoint("dna", "scout", {"challenge_level": i / 10})
    # Non-positive limit is coerced to 1, not raised.
    checkpoints = await store.list_checkpoints("dna", "scout", limit=0)
    assert len(checkpoints) == 1
    checkpoints = await store.list_checkpoints("dna", "scout", limit=-5)
    assert len(checkpoints) == 1


@pytest.mark.asyncio
async def test_list_checkpoints_respects_limit(tmp_db):
    store = LearningArtifactStore(tmp_db)
    for i in range(5):
        await store.checkpoint("dna", "scout", {"challenge_level": i / 10})
    checkpoints = await store.list_checkpoints("dna", "scout", limit=2)
    assert len(checkpoints) == 2


@pytest.mark.asyncio
async def test_owner_scoping_restore_does_not_leak(tmp_db):
    owner_a = LearningArtifactStore(tmp_db, owner_id="owner-a")
    owner_b = LearningArtifactStore(tmp_db, owner_id="owner-b")
    checkpoint_id = await owner_a.checkpoint("dna", "scout", {"challenge_level": 0.5})
    # Owner B cannot restore owner A's checkpoint, even with the same artifact_id.
    with pytest.raises(ManifestValidationError):
        await owner_b.restore("dna", "scout", checkpoint_id)


@pytest.mark.asyncio
async def test_owner_scoping_list_checkpoints_does_not_leak(tmp_db):
    owner_a = LearningArtifactStore(tmp_db, owner_id="owner-a")
    owner_b = LearningArtifactStore(tmp_db, owner_id="owner-b")
    await owner_a.checkpoint("dna", "scout", {"challenge_level": 0.5})
    await owner_b.checkpoint("dna", "scout", {"challenge_level": 0.9})
    a_checkpoints = await owner_a.list_checkpoints("dna", "scout")
    b_checkpoints = await owner_b.list_checkpoints("dna", "scout")
    assert len(a_checkpoints) == 1
    assert len(b_checkpoints) == 1
    assert a_checkpoints[0]["checkpoint_id"] != b_checkpoints[0]["checkpoint_id"]

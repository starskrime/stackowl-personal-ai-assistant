"""Story 4.9 — ``owl.reset_dna``/``owl.dna_restore``/``owl.cancel_objective``/
``owl.merge_objective`` go through commands (owls_dna_commands.py's own
handlers), and — since all four are ``severity="consequential"`` and
therefore always park at the ``/owls``/``/owl`` slash-command layer (see
``test_owls_reset_dna.py``/``test_owls_objectives.py``'s own Story 4.9
updates) — this file is where the REAL handler-level effect (and its honest
failure modes) is actually exercised.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackowl.commands.owls_dna_commands import (
    CANCEL_OBJECTIVE,
    DNA_RESTORE,
    MERGE_OBJECTIVE,
    RESET_DNA,
    DnaRestorePayload,
    ObjectivePayload,
    OwlNamePayload,
    _cancel_objective_handler,
    _dna_restore_handler,
    _merge_objective_handler,
    _reset_dna_handler,
)
from stackowl.commands.spec.context import CommandContext
from stackowl.db.pool import DbPool
from stackowl.objectives.model import Objective
from stackowl.objectives.store import ObjectiveStore
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_authored import capture_one_authored
from stackowl.owls.dna_storage import upsert_owl_dna
from stackowl.owls.learning_artifact_store import LearningArtifactStore
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def tmp_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "dna_obj.db"
    seed_schema(db_path)
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _ctx(command_type: str, command_id: str = "cmd-1") -> CommandContext:
    return CommandContext(command_id=command_id, command_type=command_type, requester_kind="owner")


def _registry_with_scout() -> OwlRegistry:
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name="scout", role="r", system_prompt="p", model_tier="fast",
            dna=OwlDNA(challenge_level=0.5),
        ),
        source_name="t",
    )
    return reg


# --------------------------------------------------------------- reset_dna


async def test_reset_dna_handler_reverts_to_authored_baseline(tmp_db: DbPool) -> None:
    reg = _registry_with_scout()
    await capture_one_authored(tmp_db, "scout", OwlDNA(challenge_level=0.5))
    await upsert_owl_dna(tmp_db, "scout", OwlDNA(challenge_level=0.8), table="owl_dna")

    token = set_services(StepServices(owl_registry=reg, db_pool=tmp_db))
    try:
        outcome = await _reset_dna_handler(OwlNamePayload(name="scout"), _ctx(RESET_DNA))
    finally:
        reset_services(token)

    assert outcome.success, outcome.error
    rows = await tmp_db.fetch_all(
        "SELECT challenge_level FROM owl_dna WHERE owl_name = ?", ("scout",),
    )
    assert rows[0]["challenge_level"] == pytest.approx(0.5)


async def test_reset_dna_handler_fails_loud_with_no_authored_baseline(tmp_db: DbPool) -> None:
    reg = _registry_with_scout()
    token = set_services(StepServices(owl_registry=reg, db_pool=tmp_db))
    try:
        outcome = await _reset_dna_handler(OwlNamePayload(name="scout"), _ctx(RESET_DNA))
    finally:
        reset_services(token)

    assert not outcome.success
    assert "no authored baseline" in (outcome.error or "").lower()


# ------------------------------------------------------------- dna_restore


async def test_dna_restore_handler_restores_exact_checkpoint(tmp_db: DbPool) -> None:
    reg = _registry_with_scout()
    store = LearningArtifactStore(tmp_db)
    checkpoint_id = await store.checkpoint(
        "dna", "scout", OwlDNA(challenge_level=0.3).model_dump(), reason="test",
    )
    await upsert_owl_dna(tmp_db, "scout", OwlDNA(challenge_level=0.9), table="owl_dna")

    token = set_services(StepServices(owl_registry=reg, db_pool=tmp_db))
    try:
        outcome = await _dna_restore_handler(
            DnaRestorePayload(name="scout", checkpoint_id=checkpoint_id), _ctx(DNA_RESTORE),
        )
    finally:
        reset_services(token)

    assert outcome.success, outcome.error
    rows = await tmp_db.fetch_all(
        "SELECT challenge_level FROM owl_dna WHERE owl_name = ?", ("scout",),
    )
    assert rows[0]["challenge_level"] == pytest.approx(0.3)


async def test_dna_restore_handler_fails_loud_on_unknown_checkpoint(tmp_db: DbPool) -> None:
    reg = _registry_with_scout()
    token = set_services(StepServices(owl_registry=reg, db_pool=tmp_db))
    try:
        outcome = await _dna_restore_handler(
            DnaRestorePayload(name="scout", checkpoint_id="not-a-real-checkpoint"),
            _ctx(DNA_RESTORE),
        )
    finally:
        reset_services(token)

    assert not outcome.success
    assert outcome.error


# --------------------------------------------------------- cancel_objective


async def _seed_objective(store: ObjectiveStore, objective_id: str, **kwargs: object) -> None:
    await store.create(Objective(
        objective_id=objective_id, owner_id=DEFAULT_PRINCIPAL_ID, intent="x", **kwargs,
    ))


async def test_cancel_objective_handler_abandons_it(tmp_db: DbPool) -> None:
    store = ObjectiveStore(tmp_db, DEFAULT_PRINCIPAL_ID)
    await _seed_objective(store, "obj-1")

    token = set_services(StepServices(db_pool=tmp_db))
    try:
        outcome = await _cancel_objective_handler(
            ObjectivePayload(objective_id="obj-1"), _ctx(CANCEL_OBJECTIVE),
        )
    finally:
        reset_services(token)

    assert outcome.success, outcome.error
    assert (await store.get("obj-1")).status == "abandoned"


async def test_cancel_objective_handler_fails_loud_on_unknown_objective(tmp_db: DbPool) -> None:
    token = set_services(StepServices(db_pool=tmp_db))
    try:
        outcome = await _cancel_objective_handler(
            ObjectivePayload(objective_id="no-such-objective"), _ctx(CANCEL_OBJECTIVE),
        )
    finally:
        reset_services(token)

    assert not outcome.success
    assert "no such objective" in (outcome.error or "").lower()


# ---------------------------------------------------------- merge_objective


async def test_merge_objective_handler_merges_and_marks_done(tmp_db: DbPool, tmp_path: Path) -> None:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    integration_branch = "stackowl/epic-obj-m1"
    subprocess.run(["git", "branch", integration_branch], cwd=repo, check=True)

    store = ObjectiveStore(tmp_db, DEFAULT_PRINCIPAL_ID)
    await _seed_objective(
        store, "obj-m1", repo=str(repo), integration_branch=integration_branch,
        base_branch="main", status="blocked", blocker="awaiting merge confirm",
        blocker_kind="decision",
    )
    [sg] = await store.add_subgoals("obj-m1", ["a"])
    await store.update_subgoal(sg.subgoal_id, "done")

    token = set_services(StepServices(db_pool=tmp_db))
    try:
        outcome = await _merge_objective_handler(
            ObjectivePayload(objective_id="obj-m1"), _ctx(MERGE_OBJECTIVE),
        )
    finally:
        reset_services(token)

    assert outcome.success, outcome.error
    reloaded = await store.get("obj-m1")
    assert reloaded.status == "done"
    current = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert current == "main"


async def test_merge_objective_handler_refuses_when_not_blocked(tmp_db: DbPool) -> None:
    store = ObjectiveStore(tmp_db, DEFAULT_PRINCIPAL_ID)
    await _seed_objective(
        store, "obj-active", repo="/tmp/nope", integration_branch="b", base_branch="main",
        status="active",
    )
    token = set_services(StepServices(db_pool=tmp_db))
    try:
        outcome = await _merge_objective_handler(
            ObjectivePayload(objective_id="obj-active"), _ctx(MERGE_OBJECTIVE),
        )
    finally:
        reset_services(token)

    assert not outcome.success
    assert "not ready to merge" in (outcome.error or "").lower()

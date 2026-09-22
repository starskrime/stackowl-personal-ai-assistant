"""Story 4.9, AC3 — "Given a reversible owl change (rename or pause), when it
completes, then undo restores it unless superseded or past 24 hours."

Drives the REAL ``owls.build.rename``/``scheduling.pause_owl_job`` command
types through ``submit_command`` + ``request_undo`` (never the tool), mirrors
``test_undo_restores_captured_prior_state.py``'s own harness shape for
Story 4.7's identical AC on scheduling.edit_job/set_owl_schedule.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import stackowl.scheduler.commands  # noqa: F401 -- registration side effect
import stackowl.tools.meta.owl_build_commands as owl_build_commands  # noqa: F401 -- registration side effect
from stackowl.commands.owls_helpers import persist_owl
from stackowl.commands.spec.submit import submit_command
from stackowl.commands.spec.undo import request_undo
from stackowl.db.pool import DbPool
from stackowl.exceptions import OwlNotFoundError
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.trigger import CronTrigger
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.scheduler.commands import PAUSE_OWL_JOB, JobLifecyclePayload
from stackowl.scheduler.owl_lifecycle import _job_id_for, reconcile_owl_schedules
from stackowl.tools.meta.owl_build_commands import (
    CREATE,
    EDIT,
    RENAME,
    RETIRE,
    OwlManifestPayload,
    RenameOwlPayload,
    RetireOwlPayload,
)
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def tmp_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "owl_undo.db"
    seed_schema(db_path)
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture(autouse=True)
def _bind_services(tmp_db: DbPool, _registry: OwlRegistry) -> Iterator[None]:
    token = set_services(StepServices(db_pool=tmp_db, owl_registry=_registry))
    yield
    reset_services(token)


@pytest.fixture()
def _registry() -> OwlRegistry:
    return OwlRegistry.with_default_secretary()


# --------------------------------------------------------------------- rename


async def test_undo_a_rename_restores_the_prior_display_name(
    tmp_db: DbPool, _registry: OwlRegistry,
) -> None:
    submission = await submit_command(
        tmp_db, RENAME, RenameOwlPayload(name="secretary", display_name="Ada", actor="secretary"),
    )
    assert submission.outcome is not None and submission.outcome.success
    assert _registry.get("secretary").display_name == "Ada"

    outcome = await request_undo(tmp_db, submission.command_id)
    assert outcome.refusal is None, outcome.refusal
    assert outcome.submission is not None and outcome.submission.outcome is not None
    assert outcome.submission.outcome.success

    # Restored to whatever the display name was BEFORE this rename (the
    # secretary's own default, not "Ada").
    assert _registry.get("secretary").display_name != "Ada"


async def test_rename_undo_refused_past_the_24h_window(
    tmp_db: DbPool, _registry: OwlRegistry, monkeypatch: pytest.MonkeyPatch,
) -> None:
    submission = await submit_command(
        tmp_db, RENAME, RenameOwlPayload(name="secretary", display_name="Ada", actor="secretary"),
    )
    assert submission.outcome is not None and submission.outcome.success

    # Push the command's own delivered_at 25 hours into the past.
    await tmp_db.execute(
        "UPDATE tasks SET delivered_at = ? WHERE command_id = ?",
        ((datetime.now(UTC) - timedelta(hours=25)).isoformat(), submission.command_id),
    )

    outcome = await request_undo(tmp_db, submission.command_id)
    assert outcome.submission is None
    assert outcome.refusal is not None
    assert outcome.refusal.code in ("expired", "window_closed", "too_late")
    # Still renamed — the refused undo changed nothing.
    assert _registry.get("secretary").display_name == "Ada"


async def test_rename_undo_refused_when_superseded_by_a_later_rename(
    tmp_db: DbPool, _registry: OwlRegistry,
) -> None:
    first = await submit_command(
        tmp_db, RENAME, RenameOwlPayload(name="secretary", display_name="Ada", actor="secretary"),
    )
    assert first.outcome is not None and first.outcome.success

    second = await submit_command(
        tmp_db, RENAME, RenameOwlPayload(name="secretary", display_name="Grace", actor="secretary"),
    )
    assert second.outcome is not None and second.outcome.success

    outcome = await request_undo(tmp_db, first.command_id)
    assert outcome.submission is None
    assert outcome.refusal is not None
    assert outcome.refusal.code == "superseded"
    # The SECOND rename's own value is still in effect — a stale undo must
    # never clobber a later command on the same target.
    assert _registry.get("secretary").display_name == "Grace"


# ---------------------------------------------------------------------- pause


async def _scheduled_owl(name: str) -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name, role="watcher", system_prompt="p", model_tier="fast",
        lifecycle="scheduled", trigger=CronTrigger(schedule="every 10m", prompt="go"),
    )


async def test_undo_a_pause_resumes_the_owned_job(
    tmp_db: DbPool, _registry: OwlRegistry,
) -> None:
    _registry.register(await _scheduled_owl("watcher"), source_name="t")
    await reconcile_owl_schedules(_registry, tmp_db)
    job_id = _job_id_for("watcher")

    submission = await submit_command(tmp_db, PAUSE_OWL_JOB, JobLifecyclePayload(job_id=job_id))
    assert submission.outcome is not None and submission.outcome.success
    rows = await tmp_db.fetch_all("SELECT enabled FROM jobs WHERE job_id = ?", (job_id,))
    assert int(rows[0]["enabled"]) == 0

    outcome = await request_undo(tmp_db, submission.command_id)
    assert outcome.refusal is None, outcome.refusal
    assert outcome.submission is not None and outcome.submission.outcome is not None
    assert outcome.submission.outcome.success

    rows = await tmp_db.fetch_all("SELECT enabled FROM jobs WHERE job_id = ?", (job_id,))
    assert int(rows[0]["enabled"]) == 1  # resumed


async def test_pause_undo_refused_past_the_24h_window(
    tmp_db: DbPool, _registry: OwlRegistry,
) -> None:
    _registry.register(await _scheduled_owl("watcher2"), source_name="t")
    await reconcile_owl_schedules(_registry, tmp_db)
    job_id = _job_id_for("watcher2")

    submission = await submit_command(tmp_db, PAUSE_OWL_JOB, JobLifecyclePayload(job_id=job_id))
    assert submission.outcome is not None and submission.outcome.success

    await tmp_db.execute(
        "UPDATE tasks SET delivered_at = ? WHERE command_id = ?",
        ((datetime.now(UTC) - timedelta(hours=25)).isoformat(), submission.command_id),
    )

    outcome = await request_undo(tmp_db, submission.command_id)
    assert outcome.submission is None
    assert outcome.refusal is not None
    rows = await tmp_db.fetch_all("SELECT enabled FROM jobs WHERE job_id = ?", (job_id,))
    assert int(rows[0]["enabled"]) == 0  # still paused — the refused undo changed nothing


# --------------------------------------------------------------------- retire


async def _persisted_agent_owl(name: str) -> OwlAgentManifest:
    manifest = OwlAgentManifest(
        name=name, role="researcher", system_prompt="p", model_tier="fast",
        origin="agent", created_by="secretary",
    )
    await persist_owl(manifest)
    return manifest


async def test_undo_a_retire_recreates_the_owl_from_its_manifest_snapshot(
    tmp_db: DbPool, _registry: OwlRegistry,
) -> None:
    manifest = await _persisted_agent_owl("scout")
    _registry.register(manifest, source_name="t")

    submission = await submit_command(tmp_db, RETIRE, RetireOwlPayload(name="scout", actor="secretary"))
    assert submission.outcome is not None and submission.outcome.success
    with pytest.raises(OwlNotFoundError):
        _registry.get("scout")

    outcome = await request_undo(tmp_db, submission.command_id)
    assert outcome.refusal is None, outcome.refusal
    assert outcome.submission is not None and outcome.submission.outcome is not None
    assert outcome.submission.outcome.success

    # The undo submitted owls.build.restore with the captured manifest —
    # the owl is back, in-memory and durably, with its original fields.
    restored = _registry.get("scout")
    assert restored.name == "scout"
    assert restored.role == "researcher"


async def test_retire_undo_refused_past_the_24h_window(
    tmp_db: DbPool, _registry: OwlRegistry,
) -> None:
    manifest = await _persisted_agent_owl("scout2")
    _registry.register(manifest, source_name="t")

    submission = await submit_command(tmp_db, RETIRE, RetireOwlPayload(name="scout2", actor="secretary"))
    assert submission.outcome is not None and submission.outcome.success

    await tmp_db.execute(
        "UPDATE tasks SET delivered_at = ? WHERE command_id = ?",
        ((datetime.now(UTC) - timedelta(hours=25)).isoformat(), submission.command_id),
    )

    outcome = await request_undo(tmp_db, submission.command_id)
    assert outcome.submission is None
    assert outcome.refusal is not None
    with pytest.raises(OwlNotFoundError):
        _registry.get("scout2")  # still retired — the refused undo changed nothing


# --------------------------------------------------------------------- create


async def test_undo_a_create_retires_the_new_owl(
    tmp_db: DbPool, _registry: OwlRegistry,
) -> None:
    """Review finding, 2026-09-22 pass: owls.build.create never captured an
    undo_payload, so request_undo's fallback resubmitted the CREATE command's
    own OwlManifestPayload straight into RetireOwlPayload's extra="forbid"
    model and raised an uncaught ValidationError. Now fixed: create's own
    handler captures {name, is_builtin, actor} explicitly."""
    manifest = OwlAgentManifest(
        name="freshling", role="researcher", system_prompt="p", model_tier="fast",
        origin="agent", created_by="secretary",
    )
    submission = await submit_command(
        tmp_db, CREATE, OwlManifestPayload(manifest=manifest, actor="secretary"),
    )
    assert submission.outcome is not None and submission.outcome.success
    assert _registry.get("freshling") is not None

    outcome = await request_undo(tmp_db, submission.command_id)
    assert outcome.refusal is None, outcome.refusal
    assert outcome.submission is not None and outcome.submission.outcome is not None
    assert outcome.submission.outcome.success

    with pytest.raises(OwlNotFoundError):
        _registry.get("freshling")


# ----------------------------------------------------------------------- edit


async def test_undo_an_edit_restores_the_prior_role(
    tmp_db: DbPool, _registry: OwlRegistry,
) -> None:
    manifest = await _persisted_agent_owl("editable")
    _registry.register(manifest, source_name="t")

    edited = manifest.model_copy(update={"role": "new role"})
    submission = await submit_command(
        tmp_db, EDIT, OwlManifestPayload(manifest=edited, actor="secretary"),
    )
    assert submission.outcome is not None and submission.outcome.success
    assert _registry.get("editable").role == "new role"

    outcome = await request_undo(tmp_db, submission.command_id)
    assert outcome.refusal is None, outcome.refusal
    assert outcome.submission is not None and outcome.submission.outcome is not None
    assert outcome.submission.outcome.success

    assert _registry.get("editable").role == "researcher"

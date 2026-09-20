"""The objective ``NameResolver`` (AD-30): wire ``objectives`` INTO
``journal/narrator.py`` for ``RecordKind.OBJECTIVE`` (Story 4.7).

Mirrors ``scheduler/journal_names.py``'s shape exactly: ``journal/`` imports
nothing from any subsystem (AD-7), so the owning subsystem registers a
resolver INTO ``journal/`` rather than the other way round. Without this,
every ``objective.set`` narration falls back to the narrator's generic
tombstone (graceful degradation, ``journal/narrator.py``) instead of the
objective's own intent — registered, not required, but wrong on every hit
until it is.
"""

from __future__ import annotations

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.journal import RecordKind, register_name_resolver
from stackowl.journal.objective_events import _MAX_LABEL_LEN
from stackowl.objectives.store import ObjectiveNotFoundError, ObjectiveStore
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID


async def resolve_objective_name(db: DbPool, objective_id: str) -> str | None:
    """Return an objective's ``intent`` (truncated to AD-4's bound), or
    ``None`` if it no longer exists. Goes through :class:`ObjectiveStore`
    (never a raw query) so the lookup stays owner-scoped like every other
    read of this owner-governed table — mirrors ``pipeline/durable/
    journal_names.py::resolve_task_name``'s identical "owned store, never a
    bare SELECT" shape for the same reason."""
    log.journal.debug(
        "[journal_names] resolve_objective_name: entry",
        extra={"_fields": {"objective_id": objective_id}},
    )
    store = ObjectiveStore(db, DEFAULT_PRINCIPAL_ID)
    try:
        objective = await store.get(objective_id)
    except ObjectiveNotFoundError:
        # A miss is expected (retired/cross-owner), not an error.
        log.journal.debug(
            "[journal_names] resolve_objective_name: objective not found -- no name",
            extra={"_fields": {"objective_id": objective_id}},
        )
        return None
    name = objective.intent[:_MAX_LABEL_LEN]
    log.journal.debug(
        "[journal_names] resolve_objective_name: exit -- resolved",
        extra={"_fields": {"objective_id": objective_id}},
    )
    return name


def register_objective_name_resolver(db_pool: DbPool) -> None:
    """Register the objective ``NameResolver`` for :attr:`RecordKind.OBJECTIVE`.

    Called once at boot (``startup/orchestrator.py``), alongside
    ``register_job_name_resolver``/``register_subsystem_name_resolver``.
    """

    async def _resolver(objective_id: str) -> str | None:
        return await resolve_objective_name(db_pool, objective_id)

    register_name_resolver(RecordKind.OBJECTIVE, _resolver)
    log.journal.info(
        "[journal_names] register_objective_name_resolver: registered"
    )

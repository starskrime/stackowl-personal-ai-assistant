"""The task ``NameResolver`` (AD-30): wires ``pipeline.durable`` INTO
``journal/narrator.py`` as the first (and today only) registrant, keyed by
``RecordKind.TASK``.

``journal/`` imports nothing from any subsystem (AD-7) -- this module lives
in ``pipeline.durable`` precisely so the dependency runs the other way: the
owning subsystem registers a resolver INTO ``journal/`` rather than
``journal/`` importing ``DurableTaskStore`` itself.
"""

from __future__ import annotations

from stackowl.db.pool import DbPool
from stackowl.exceptions import DurableTaskNotFoundError
from stackowl.infra.observability import log
from stackowl.journal import RecordKind, register_name_resolver
from stackowl.journal.task_events import _MAX_LABEL_LEN
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID


async def resolve_task_name(store: DurableTaskStore, task_id: str) -> str | None:
    """Return a task's current display name, or ``None`` if it no longer exists.

    ``None`` (not an exception) is the narrator's cue to fall back to its
    tombstone text -- a task that finished, was cleaned up, or never belonged
    to the bound owner all look the same here: no name to show.
    """
    # 1. ENTRY
    log.journal.debug(
        "[journal_names] resolve_task_name: entry",
        extra={"_fields": {"task_id": task_id}},
    )
    try:
        task = await store.get(task_id)
    except DurableTaskNotFoundError:
        # 2. DECISION -- a miss is expected (task gone/cross-owner), not an error.
        log.journal.debug(
            "[journal_names] resolve_task_name: task not found -- no name",
            extra={"_fields": {"task_id": task_id}},
        )
        return None
    # 3. STEP -- same 64-char bounded-label convention as every other journal
    # attrs field (AD-4's `_MAX_LABEL_LEN`); a goal is free text and must be
    # truncated before it reaches any journal-adjacent surface.
    name = task.goal[:_MAX_LABEL_LEN]
    # 4. EXIT
    log.journal.debug(
        "[journal_names] resolve_task_name: exit -- resolved",
        extra={"_fields": {"task_id": task_id}},
    )
    return name


def register_task_name_resolver(
    db_pool: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID,
) -> None:
    """Register the task ``NameResolver`` for ``RecordKind.TASK``.

    Builds one owner-scoped ``DurableTaskStore`` and closes over it -- called
    once at boot (``startup/orchestrator.py``'s ``_phase_gateway``), alongside
    the process's other ``DurableTaskStore`` construction.
    """
    store = DurableTaskStore(db_pool, owner_id)

    async def _resolver(task_id: str) -> str | None:
        return await resolve_task_name(store, task_id)

    register_name_resolver(RecordKind.TASK, _resolver)
    log.journal.info(
        "[journal_names] register_task_name_resolver: registered",
        extra={"_fields": {"owner_id": owner_id}},
    )

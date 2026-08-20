"""A task row is owned by a PRINCIPAL, never by a knowledge scope.

MEASURED ON THE LIVE TABLE 2026-08-20. 73 rows sat ``pending`` — the oldest since
2026-08-19T03:43, every one of them past its ``next_attempt_at`` — and the loop
had never touched 72 of them. Not a backlog: they were UNREACHABLE.

    tasks by owner_id           principal-default  759 failed / 33 completed / 1 pending
                                72055773            96 completed / 57 pending / 30 failed
                                owl:Brain:recovery:task-fb27837   ... and ~18 more lanes

``TaskLoop`` is constructed once, ``TaskLoop(store=DurableTaskStore(db_pool))``,
with no owner — so it is bound to ``principal-default`` and ``claimable`` carries
``WHERE owner_id = ?``. Every row filed under anything else is invisible to it
forever. loop.py's own docstring names this exact state as the thing worse than
having no loop: "work accumulates in a table nobody is draining, and nothing
reports it."

ONE WRITER PRODUCED ALL OF THEM. Classified by task-id prefix, every non-principal
row is a ``rollover-*`` row — 387 in total — and every ``task-*``, ``child-*`` and
chat-turn row is correctly under the principal.

THE CATEGORY ERROR. ``rollover_summary_handler`` computes
``scope = identity_key or session_key`` — that is ``owner_scope_key``, which
answers *who is this knowledge ABOUT*. Correct for the memory write. It then
passes the same string as ``tasks.owner_id``, which answers a different question:
*which principal owns this WORK*. That is the tenancy boundary the loop, the
liveness sweep and every ``_require_owned`` check turn on.

It is also CONDITIONAL, which is why the rows scatter: identity resolves and the
row lands under a Telegram chat id, identity does not and the same conversation's
row lands under a lane string. That is ESC-17's shape exactly, observed in the
wild rather than reasoned about.

The knowledge scope keeps the summary. The principal keeps the task — and the
``owner`` parameter that carried the scope into all three seams is gone, because a
parameter that looks load-bearing and is not is how this comes back.
"""

from __future__ import annotations

import pytest

from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

pytestmark = pytest.mark.asyncio


class _CapturingStore:
    """Records the owner every DurableTaskStore is constructed with."""

    owners: list[str] = []

    def __init__(self, db: object, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        type(self).owners.append(owner_id)
        self.owner_id = owner_id

    async def create(self, task: object) -> None:
        self.created = task

    async def save_checkpoint(self, task_id: str, blob: str) -> None:
        return None

    async def update_status(self, task_id: str, status: str, **kw: object) -> None:
        return None


class TestTheCheckpointRowIsFiledUnderThePrincipal:
    async def test_open_close_and_checkpoint_all_use_the_principal(
        self, monkeypatch
    ) -> None:
        """All three seams, because filing the row under one owner and closing it
        under another would strand it just as thoroughly."""
        from stackowl.memory import rollover_summary_handler as mod

        _CapturingStore.owners = []
        monkeypatch.setattr(mod, "DurableTaskStore", _CapturingStore)

        handler = mod.RolloverSummaryHandler.__new__(mod.RolloverSummaryHandler)
        handler._db = object()  # noqa: SLF001

        job = type("J", (), {"job_id": "j1", "params": {}})()
        task_id = await handler._open_task(  # noqa: SLF001
            job, lane="owl:secretary:telegram:dm:72055773", ended="20260820_1",
        )
        await handler._checkpoint(task_id, blob="{}")  # noqa: SLF001
        await handler._close_task(  # noqa: SLF001
            task_id, status="completed", result="ok",
        )

        assert _CapturingStore.owners == [DEFAULT_PRINCIPAL_ID] * 3, (
            f"a knowledge scope reached tasks.owner_id: {_CapturingStore.owners}"
        )

    async def test_the_row_itself_carries_the_principal(self, monkeypatch) -> None:
        """The store's binding and the row's own column must agree — ``create``
        writes ``owner_id`` from the model, so a principal-bound store holding a
        scope-stamped row would still be unreachable."""
        from stackowl.memory import rollover_summary_handler as mod

        captured: dict[str, object] = {}

        class _Store(_CapturingStore):
            async def create(self, task: object) -> None:
                captured["owner_id"] = task.owner_id

        _CapturingStore.owners = []
        monkeypatch.setattr(mod, "DurableTaskStore", _Store)

        handler = mod.RolloverSummaryHandler.__new__(mod.RolloverSummaryHandler)
        handler._db = object()  # noqa: SLF001
        job = type("J", (), {"job_id": "j1", "params": {}})()

        await handler._open_task(  # noqa: SLF001
            job, lane="owl:Brain:recovery:task-fb27837", ended="20260820_1",
        )

        assert captured["owner_id"] == DEFAULT_PRINCIPAL_ID

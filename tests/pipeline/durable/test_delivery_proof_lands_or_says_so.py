"""A delivery proof that matched no row must SAY SO.

``mark_delivered`` is, in Bakir's words, "the only way a task completes". It ran
its UPDATE and logged "[loop] task COMPLETE — its outcome reached its destination"
without ever asking how many rows it matched.

THAT IS WHY THE REAL DEFECT SURVIVED. The chat completion seam keyed the proof on
``trace_id`` while the durable row is keyed by ``task_id``. For an ordinary turn
the two are the same string, so it worked. A RECOVERY drive mints
``trace_id="recover-<12hex>"`` and the proof was written against an id that does
not exist — zero rows updated, and the log still said COMPLETE. The code, the log
line and the tests all agreed with each other and were all wrong.

So the fix to the caller is not enough on its own: the write must be able to
disagree with the claim. This pins the effect, not the call.
"""

from __future__ import annotations

import datetime

import pytest
from tests._schema_template import seed_schema

from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask

pytestmark = pytest.mark.asyncio

UTC = datetime.UTC


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))

    db = DbPool(db_path=tmp_path / "t.db")
    await db.open()
    seed_schema(tmp_path / "t.db")
    yield DurableTaskStore(db)
    await db.close()


class TestAProofThatLandsOnNothing:
    async def test_marking_an_unknown_id_delivered_is_an_error_not_a_success(
        self, store: DurableTaskStore, caplog
    ) -> None:
        """The exact live shape: a recovery drive's trace id, which is not a task id.

        The row it was meant to prove stays undelivered, so the loop must be told —
        a silent no-op here is a delivered answer the platform believes it lost, or
        a lost one it believes was delivered.
        """
        with caplog.at_level("ERROR"):
            await store.mark_delivered("recover-f1f930de554a", result="the answer")

        assert any(
            "delivery proof matched NO row" in r.message for r in caplog.records
        ), f"silent no-op; records were {[r.message for r in caplog.records]}"

    async def test_marking_a_real_task_delivered_stamps_the_proof(
        self, store: DurableTaskStore, caplog
    ) -> None:
        """The happy path must be byte-identical — and must NOT log the error."""
        now = datetime.datetime.now(tz=UTC)
        await store.enqueue(
            DurableTask(
                task_id="t-real",
                goal="answer the question",
                status="running",
                trigger_kind="chat",
                destination="telegram:72055773",
                created_at=now,
                updated_at=now,
            )
        )

        with caplog.at_level("ERROR"):
            await store.mark_delivered("t-real", result="the answer is 42")

        task = await store.get("t-real")
        assert task.status == "completed"
        assert task.delivered_at is not None
        assert task.result == "the answer is 42"
        assert not any("matched NO row" in r.message for r in caplog.records)

"""Tests: DreamWorker wires ConversationMiner (RC-A Tasks 2-3).

Verifies:
- _mine() calls miner.mine_all() and returns its result.
- _mine() is None-safe (returns 0 when no miner wired).
- _mine() self-heals on miner failure (returns 0) and logs at ERROR (loud).
"""

import logging

import pytest


class _SpyMiner:
    def __init__(self, result=3, raises=False):
        self.called = 0
        self._result = result
        self._raises = raises

    async def mine_all(self, budget_s: float | None = None):
        # DEBT-32 — the real mine_all now takes a time budget, and a double whose
        # signature has drifted from the interface it stands in for tests nothing.
        # Recorded rather than quietly widened: this test broke because the
        # PRODUCTION signature changed, which is the double doing its job.
        self.called += 1
        self.budget_s = budget_s
        if self._raises:
            raise RuntimeError("boom")
        return self._result


@pytest.mark.asyncio
async def test_dreamworker_runs_miner():
    from stackowl.memory.dream_worker import DreamWorkerJobHandler

    spy = _SpyMiner()
    h = DreamWorkerJobHandler(
        bridge=None, promoter=None, pruner=None,
        kuzu_handler=None, detector=None, miner=spy,
    )
    assert await h._mine() == 3 and spy.called == 1


@pytest.mark.asyncio
async def test_dreamworker_mine_none_safe():
    from stackowl.memory.dream_worker import DreamWorkerJobHandler

    h = DreamWorkerJobHandler(
        bridge=None, promoter=None, pruner=None,
        kuzu_handler=None, detector=None,
    )
    assert await h._mine() == 0


@pytest.mark.asyncio
async def test_dreamworker_mine_failure_is_loud_not_fatal(caplog):
    from stackowl.memory.dream_worker import DreamWorkerJobHandler

    spy = _SpyMiner(raises=True)
    h = DreamWorkerJobHandler(
        bridge=None, promoter=None, pruner=None,
        kuzu_handler=None, detector=None, miner=spy,
    )
    with caplog.at_level(logging.ERROR, logger="stackowl.memory"):
        assert await h._mine() == 0  # self-heals
    assert any(r.levelno >= logging.ERROR for r in caplog.records)  # loud

"""SEC-3 — defense-in-depth depth/bounds self-checks (F163 execute_code, F164 batch).

F163: execute_code self-defends — a delegated sub-agent (delegation_depth>0) is
refused at the TOOL entry, not only by the pipeline schema filter.
F164: batch_approve's executor re-applies the child-exclusion guard per action, so
an approved batch cannot run a child-excluded action when delegation_depth>0.
"""

from __future__ import annotations

import pytest

from stackowl.infra.trace import TraceContext
from stackowl.tools.child_exclusion import CHILD_EXCLUDED_TOOLS, child_excluded_now


class TestChildExclusionHelper:
    def test_canonical_set_contains_execute_code_and_spawn(self) -> None:
        assert "execute_code" in CHILD_EXCLUDED_TOOLS
        assert "delegate_task" in CHILD_EXCLUDED_TOOLS

    def test_child_excluded_now_true_only_for_depth_gt0(self) -> None:
        token = TraceContext.start(delegation_depth=0)
        try:
            assert child_excluded_now("execute_code") is False
        finally:
            TraceContext.reset(token)
        token = TraceContext.start(delegation_depth=2)
        try:
            assert child_excluded_now("execute_code") is True
            assert child_excluded_now("read_file") is False  # not child-excluded
        finally:
            TraceContext.reset(token)


@pytest.mark.asyncio
class TestExecuteCodeSelfDefense:
    async def test_execute_code_refuses_at_depth_gt0(self) -> None:
        """F163 — a delegated child calling execute_code is refused by the TOOL.

        No sandbox selector / pipeline filter is involved here: the refusal is the
        tool's own entry-time delegation_depth assertion.
        """
        from stackowl.tools.code.execute_code import ExecuteCodeTool

        token = TraceContext.start(delegation_depth=1)
        try:
            res = await ExecuteCodeTool().execute(code="print(1)", language="python")
        finally:
            TraceContext.reset(token)
        assert res.success is False
        assert "deleg" in (res.error or "").lower()


@pytest.mark.asyncio
class TestBatchActionChildExclusion:
    async def test_batch_executor_refuses_child_excluded_action_at_depth(self) -> None:
        """F164 — an approved batch cannot execute a child-excluded action at depth.

        The BatchExecutor re-applies the per-action child-exclusion guard so a
        pre-consented batch (the per-action dispatch gate is bypassed by design)
        still cannot smuggle a fork-bomb tool past the depth rail.
        """
        from stackowl.tools.base import Tool, ToolResult
        from stackowl.tools.interaction._batch_support import (
            BatchAction,
            BatchApproveArgs,
            BatchExecutor,
        )
        from stackowl.tools.registry import ToolRegistry

        class _FakeExecuteCode(Tool):
            ran = False

            @property
            def name(self) -> str:
                return "execute_code"

            @property
            def description(self) -> str:
                return "fake"

            @property
            def parameters(self) -> dict[str, object]:
                return {"type": "object", "properties": {}}

            async def execute(self, **kwargs: object) -> ToolResult:
                _FakeExecuteCode.ran = True
                return ToolResult(success=True, output="ran", duration_ms=0)

        reg = ToolRegistry()
        reg.register(_FakeExecuteCode())

        class _NoAudit:
            def grant(self, *a: object, **k: object) -> None: ...
            def action(self, *a: object, **k: object) -> None: ...

        executor = BatchExecutor(reg, _NoAudit())  # type: ignore[arg-type]
        args = BatchApproveArgs(
            intro="x",
            actions=[BatchAction(tool="execute_code", args={}, summary="run code")],
        )

        token = TraceContext.start(delegation_depth=1)
        try:
            outcomes, n_ok, n_fail = await executor.run(args, "sess")
        finally:
            TraceContext.reset(token)

        assert _FakeExecuteCode.ran is False  # the child-excluded action never ran
        assert n_ok == 0
        assert n_fail == 1
        assert "deleg" in str(outcomes[0]["error"]).lower()

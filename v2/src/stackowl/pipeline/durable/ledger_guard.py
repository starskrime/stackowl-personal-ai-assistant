"""ledger_guard — route side-effecting tool calls through the exactly-once ledger.

This is the single seam that makes a side-effecting tool run **at most once**
across crashes/replays of a durable ReAct task.  It wraps the real tool call at
the dispatch chokepoint (``execute._dispatch``) and is **dormant** unless a
:class:`~stackowl.pipeline.durable.context.DurableReActContext` is active:

* No active durable context  → ``await execute_fn()`` (exact current behavior).
* Active context, but a ``read`` (non-side-effecting) tool → ``await
  execute_fn()`` (pure reads are replay-safe, never ledgered).
* Active context + side-effecting tool → consult the ledger:
    - ``already_committed`` → return the recorded result WITHOUT re-executing.
    - ``proceed``           → execute exactly once, then ``commit`` the result.
    - ``uncertain``         → raise :class:`DurableReplayUncertain` (park; never
                              re-run a possibly half-done side effect).

Result (de)serialization contract
---------------------------------
The ledger stores a single string per committed call.  Tool calls at the
dispatch seam return a :class:`~stackowl.tools.base.ToolResult` (a frozen
Pydantic model).  The contract is therefore:

* **serialize**   — a ``ToolResult`` is stored as its canonical JSON
  (``model_dump_json``); any other result type falls back to ``str(result)``.
* **deserialize** — on ``already_committed`` the stored blob is parsed back into
  a ``ToolResult`` via ``model_validate_json``; if it was a plain string (legacy
  / non-ToolResult), it is wrapped into a successful ``ToolResult`` carrying the
  string as ``output`` so the dispatch path sees a uniform shape.

This keeps the stored form deterministic and the replay value type-stable for
the caller, without coupling the guard to any specific tool.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from stackowl.exceptions import DurableReplayUncertain
from stackowl.infra.observability import log
from stackowl.pipeline.durable.context import get_active
from stackowl.pipeline.durable.ledger import is_side_effecting
from stackowl.tools.base import ToolResult


def _serialize_result(result: Any) -> str:
    """Serialize a tool result to a stable string for the ledger.

    A :class:`ToolResult` becomes its canonical JSON; anything else falls back
    to ``str()``.  Deterministic and lossless for ToolResult (the only shape the
    dispatch seam produces).
    """
    if isinstance(result, ToolResult):
        return result.model_dump_json()
    return str(result)


def _deserialize_result(blob: str | None) -> ToolResult:
    """Rebuild a :class:`ToolResult` from a committed ledger blob.

    Inverse of :func:`_serialize_result`.  A JSON ToolResult is validated back
    into one; a plain string (or ``None``) is wrapped into a successful
    ToolResult carrying it as ``output`` so the caller always gets a uniform
    type on replay.
    """
    text = "" if blob is None else blob
    try:
        return ToolResult.model_validate_json(text)
    except ValueError:
        # Not a ToolResult JSON blob — treat the stored string as the output of
        # a successful call (replay-safe uniform shape). duration_ms is unknown
        # on replay (the call did not actually run now), so report 0.0.
        return ToolResult(success=True, output=text, error=None, duration_ms=0.0)


async def ledger_guard(
    tool_name: str,
    args: dict[str, object],
    action_severity: str,
    execute_fn: Callable[[], Awaitable[ToolResult]],
) -> ToolResult:
    """Run ``execute_fn`` under exactly-once semantics when a durable task is active.

    ``execute_fn`` is a zero-arg async callable performing the real tool call and
    returning its :class:`ToolResult`.  ``action_severity`` is the tool's trusted
    ``ToolManifest.action_severity`` (read/write/consequential).

    Dormant by default: with no active :class:`DurableReActContext` this is a
    transparent ``await execute_fn()`` — byte-for-byte current behavior.
    """
    ctx = get_active()

    # DORMANT — no durable task running: exact current behavior, no ledger.
    if ctx is None:
        log.tasks.debug(
            "[tasks] ledger_guard: no active durable context — passthrough",
            extra={"_fields": {"tool_name": tool_name}},
        )
        return await execute_fn()

    # Active context, but a pure/read tool: replay-safe, never ledger-guarded.
    if not is_side_effecting(action_severity):
        log.tasks.debug(
            "[tasks] ledger_guard: read/pure tool under durable ctx — passthrough",
            extra={"_fields": {
                "tool_name": tool_name, "task_id": ctx.task_id,
                "iteration": ctx.iteration, "action_severity": action_severity,
            }},
        )
        return await execute_fn()

    # 1. ENTRY — side-effecting call under an active durable context.
    log.tasks.debug(
        "[tasks] ledger_guard: side-effecting under durable ctx — consulting ledger",
        extra={"_fields": {
            "tool_name": tool_name, "task_id": ctx.task_id,
            "iteration": ctx.iteration, "action_severity": action_severity,
        }},
    )
    decision = await ctx.ledger.begin(ctx.task_id, ctx.iteration, tool_name, args)

    # 2. DECISION — branch on the ledger verdict.
    if decision.outcome == "already_committed":
        # Replay: the side effect already happened — DO NOT re-execute.
        log.tasks.info(
            "[tasks] ledger_guard: already committed — replaying recorded result",
            extra={"_fields": {
                "tool_name": tool_name, "task_id": ctx.task_id, "iteration": ctx.iteration,
            }},
        )
        return _deserialize_result(decision.result)

    if decision.outcome == "uncertain":
        # Intent without commit: a prior attempt may have half-run the side
        # effect. Refuse to re-run blindly — surface so the executor can park.
        log.tasks.warning(
            "[tasks] ledger_guard: uncertain — intent without commit, parking",
            extra={"_fields": {
                "tool_name": tool_name, "task_id": ctx.task_id, "iteration": ctx.iteration,
            }},
        )
        raise DurableReplayUncertain(ctx.task_id, ctx.iteration, tool_name)

    # 3. STEP — proceed: run the side effect exactly once, then commit.
    result = await execute_fn()
    serialized = _serialize_result(result)
    await ctx.ledger.commit(ctx.task_id, ctx.iteration, tool_name, args, serialized)

    # 4. EXIT
    log.tasks.debug(
        "[tasks] ledger_guard: exit — executed once and committed",
        extra={"_fields": {
            "tool_name": tool_name, "task_id": ctx.task_id,
            "iteration": ctx.iteration, "result_len": len(serialized),
        }},
    )
    return result

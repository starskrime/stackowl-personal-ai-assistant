"""todo — track and MUTATE the working checklist of steps for the current task.

A thin action-dispatching tool over the shared :class:`PlanStore` (the one plan
slot also written by ``update_plan``). ``todo`` mutates *individual* items:

* ``action='add'`` / ``action='merge'`` — update existing items by id and append
  new ones (the working incremental path);
* ``action='replace'`` — swap the whole list for a fresh ``items`` array;
* ``action='set_status'`` — flip one item's status by id;
* ``action='list'`` — read the current plan (no mutation).

Every call returns the rendered plan (via the store's injection format) so the
checklist survives context compaction.

Severity (operator decision): ``read`` — it mutates only an in-memory, ephemeral
plan used for context re-injection. No filesystem / external effect, no consent
needed. ``toolset_group="planning"`` (operator decision): a dedicated planning
group, distinct from ``knowledge`` (durable facts) and ``code`` (file writes).

The single-in_progress invariant is AUTO-CORRECTED by the store (extra
``in_progress`` items are demoted to ``pending`` + logged) — never a hard reject.

Provenance / port-vs-build: PORT of the in-memory todo substrate; see
``_bmad-output/research/tool-port-analysis.md`` (E5 ``todo`` row).
port-source: upstream-agent.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.planning.store import VALID_STATUSES, PlanStore

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from collections.abc import Mapping

_VALID_ACTIONS: tuple[str, ...] = ("add", "merge", "replace", "set_status", "list")
_MUTATING_ITEM_ACTIONS = frozenset({"add", "merge", "replace"})


def _did_you_mean(action: str) -> str:
    """Structured 'did you mean' for an unknown action enum value."""
    valid = "|".join(_VALID_ACTIONS)
    suggestion = next((a for a in _VALID_ACTIONS if action and a[0] == action[0]), None)
    hint = f" Did you mean '{suggestion}'?" if suggestion else ""
    return f"Unknown action {action!r}. Valid actions: {valid}.{hint}"


class TodoTool(Tool):
    """Track/mutate the working checklist of steps (shared plan slot)."""

    def __init__(self, store: PlanStore | None = None) -> None:
        # The store is injected so ``todo`` and ``update_plan`` share ONE slot.
        self._store = store or PlanStore()

    @property
    def name(self) -> str:
        return "todo"

    @property
    def description(self) -> str:
        return (
            "Track and MUTATE the working checklist of steps for the current "
            "task. action='add'/'merge' updates items by id and appends new "
            "ones; action='replace' swaps the whole list for a fresh 'items' "
            "array; action='set_status' flips one item's status by id; "
            "action='list' reads the plan. Each item is "
            "{id, content, status:pending|in_progress|completed|cancelled}; "
            "list order is priority; keep ONE item in_progress at a time. "
            "Always returns the full current plan. "
            "LANE: the incremental working checklist — add a step, mark one "
            "done, tweak one item. "
            "ANTI-LANE: to replace the WHOLE plan in one shot (with an "
            "explanation) use update_plan. NOT memory (that is durable facts). "
            "NOT skills (those are how-to procedures)."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_VALID_ACTIONS),
                    "description": "add | merge | replace | set_status | list",
                },
                "items": {
                    "type": "array",
                    "description": (
                        "Plan items for add/merge/replace. Each: "
                        "{id, content, status}."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Unique item id."},
                            "content": {
                                "type": "string",
                                "description": "Task description.",
                            },
                            "status": {
                                "type": "string",
                                "enum": sorted(VALID_STATUSES),
                                "description": "Item status.",
                            },
                        },
                        "required": ["id", "content"],
                    },
                },
                "id": {
                    "type": "string",
                    "description": "Item id for action='set_status'.",
                },
                "status": {
                    "type": "string",
                    "enum": sorted(VALID_STATUSES),
                    "description": "New status for action='set_status'.",
                },
            },
            "required": ["action"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            toolset_group="planning",
        )

    # ------------------------------------------------------------------ dispatch

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        action = str(kwargs.get("action", "")).strip().lower()
        # 1. ENTRY
        log.tool.info("todo.execute: entry", extra={"_fields": {"action": action}})

        if action not in _VALID_ACTIONS:
            return self._err(_did_you_mean(action), t0)

        try:
            # 2. DECISION — dispatch by validated action.
            if action == "list":
                return self._ok(t0, action)
            if action == "set_status":
                return self._set_status(kwargs, t0)
            # add / merge / replace
            items = self._coerce_items(kwargs.get("items"))
            if not items:
                return self._err(
                    f"action='{action}' requires a non-empty 'items' array.", t0
                )
            if action == "replace":
                self._store.replace(items)
            else:  # add or merge — both update-by-id + append
                self._store.merge(items)
            return self._ok(t0, action)
        except Exception as exc:  # self-healing — degrade, never raise.
            log.tool.error(
                "todo.execute: action failed — degrading to structured error",
                exc_info=exc,
                extra={"_fields": {"action": action}},
            )
            return self._err(f"{type(exc).__name__}: {exc}", t0)

    # ------------------------------------------------------------------- actions

    def _set_status(self, kwargs: dict[str, object], t0: float) -> ToolResult:
        item_id = str(kwargs.get("id", "")).strip()
        status = str(kwargs.get("status", "")).strip().lower()
        if not item_id:
            return self._err("action='set_status' requires 'id'.", t0)
        if status not in VALID_STATUSES:
            valid = "|".join(sorted(VALID_STATUSES))
            return self._err(
                f"action='set_status' requires a valid 'status' ({valid}).", t0
            )
        # Merge a single status update — the store applies it by id + auto-corrects.
        current_ids = {it.id for it in self._store.read()}
        if item_id not in current_ids:
            return self._err(f"No plan item with id {item_id!r}.", t0)
        self._store.merge([{"id": item_id, "status": status}])
        return self._ok(t0, "set_status")

    # ------------------------------------------------------------------- helpers

    @staticmethod
    def _coerce_items(raw: object) -> list[Mapping[str, object]]:
        """Pull out the mapping items from raw input — junk is skipped, never raised."""
        from collections.abc import Mapping as _Mapping

        if not isinstance(raw, (list, tuple)):
            return []
        return [it for it in raw if isinstance(it, _Mapping)]

    def _ok(self, t0: float, action: str) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        rendered = self._store.format_for_injection() or "(plan is empty)"
        demoted = self._store.last_demoted()
        if demoted:
            rendered += (
                f"\nnote: {len(demoted)} item(s) auto-demoted to pending "
                "(only one step may be in_progress at a time)."
            )
        counts = self._store.counts()
        log.tool.info(
            "todo.execute: exit",
            extra={
                "_fields": {
                    "success": True,
                    "action": action,
                    "total": counts["total"],
                    "duration_ms": duration_ms,
                }
            },
        )
        return ToolResult(success=True, output=rendered, duration_ms=duration_ms)

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "todo.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)

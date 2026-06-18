"""PlanStore — the in-memory working-checklist shared by ``todo`` + ``update_plan``.

A single process-level store holding the current plan: an ordered list of items
``{id, content, status}`` where ``status`` is one of
``pending | in_progress | completed | cancelled``. List position is priority.

Two tools write this one slot (operator decision — single source of truth):

* ``todo`` mutates individual items — ``replace`` swaps the whole list,
  ``merge`` updates existing items by id and appends new ones.
* ``update_plan`` replaces the WHOLE plan at once ({explanation, plan[]}).

Both go through the same invariants here:

* dedup by id (last occurrence wins, keeping the earliest position);
* the **single-in_progress invariant is AUTO-CORRECTED, never hard-rejected** —
  if more than one item is ``in_progress`` after a write, all but the FIRST are
  demoted to ``pending`` and the demotion is logged. Mid-plan, the model must
  never be hard-blocked for a benign over-emit.

``format_for_injection()`` renders the active list so the checklist survives
context compaction by being re-injected into the message history.

Lifetime note: this store is **process-level** — there is no per-session signal
yet, so a single agent loop (the common case) is assumed. This matches the
limitation of the sibling write-safety / undo substrate; revisit when a
per-session plan slot lands.

Provenance / port-vs-build: PORT of the in-memory todo-list substrate
(ordered list, replace/merge modes, dedup-by-id, status enum,
``format_for_injection()`` for post-compaction re-injection); see
``_bmad-output/research/tool-port-analysis.md`` (E5 ``todo`` row).
port-source: upstream-agent.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from stackowl.infra.observability import log

# Valid status values for a plan item. Confirmed 1:1 against the ported
# in-memory todo substrate (pending / in_progress / completed / cancelled).
VALID_STATUSES: frozenset[str] = frozenset(
    {"pending", "in_progress", "completed", "cancelled"}
)

_DEFAULT_STATUS = "pending"
_IN_PROGRESS = "in_progress"
# Cap on items rendered into the post-compaction re-injection block — an
# unbounded plan re-injected every turn would blow the context budget (the
# opposite of the compaction-survival goal). Programmatic reads stay uncapped.
_MAX_INJECTED_ITEMS = 50

# Compact status markers for the injected render.
_MARKERS: dict[str, str] = {
    "completed": "[x]",
    "in_progress": "[>]",
    "pending": "[ ]",
    "cancelled": "[~]",
}
# Statuses worth re-injecting after compaction — completed/cancelled items are
# omitted so the model does not re-do finished work.
_ACTIVE_STATUSES: frozenset[str] = frozenset({"pending", "in_progress"})


@dataclass
class PlanItem:
    """One plan/checklist item. List position is priority."""

    id: str
    content: str
    status: str

    def copy(self) -> PlanItem:
        return PlanItem(id=self.id, content=self.content, status=self.status)

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "content": self.content, "status": self.status}


class PlanStore:
    """In-memory ordered plan, shared by ``todo`` and ``update_plan``.

    Self-healing: malformed input items are normalised (never raise) — a missing
    id becomes ``"?"``, a missing content becomes a placeholder, an unknown
    status falls back to ``pending``. Over-emitted ``in_progress`` is
    auto-corrected, not rejected.
    """

    def __init__(self) -> None:
        self._items: list[PlanItem] = []
        # ids demoted by the most recent single-in_progress auto-correction, so
        # the calling tool can surface a note to the model.
        self._last_demoted: list[str] = []

    # ----------------------------------------------------------------- mutations

    def replace(self, items: Iterable[Mapping[str, object]]) -> list[PlanItem]:
        """Swap the whole list for *items* (dedup by id, single-in_progress fix)."""
        deduped = self._dedupe_by_id(items)
        self._items = [self._normalise(it) for it in deduped]
        # Whole-plan replace carries no per-item intent → keep the first in_progress.
        self._enforce_single_in_progress(prefer_id=None)
        return self.read()

    def merge(self, items: Iterable[Mapping[str, object]]) -> list[PlanItem]:
        """Update existing items by id and append new ones (order preserved)."""
        by_id = {item.id: item for item in self._items}
        touched_in_progress: str | None = None
        for it in self._dedupe_by_id(items):
            item_id = self._coerce_str(it.get("id")).strip()
            if not item_id:
                continue  # cannot merge without an id — skip (self-healing)
            status = self._coerce_str(it.get("status")).strip().lower()
            if status == _IN_PROGRESS:
                touched_in_progress = item_id  # last explicitly-started item wins
            if item_id in by_id:
                existing = by_id[item_id]
                content = self._coerce_str(it.get("content")).strip()
                if content:
                    existing.content = content
                if status in VALID_STATUSES:
                    existing.status = status
            else:
                normalised = self._normalise(it)
                by_id[normalised.id] = normalised
                self._items.append(normalised)
        # Rebuild preserving the existing order; the dict carries any updates.
        seen: set[str] = set()
        rebuilt: list[PlanItem] = []
        for item in self._items:
            current = by_id.get(item.id, item)
            if current.id not in seen:
                rebuilt.append(current)
                seen.add(current.id)
        self._items = rebuilt
        # The item the caller just set in_progress wins — so "start step 2" while
        # step 1 is active demotes step 1, not the model's new intent.
        self._enforce_single_in_progress(prefer_id=touched_in_progress)
        return self.read()

    def clear(self) -> list[PlanItem]:
        """Empty the plan (used when ``update_plan`` is given an empty plan[])."""
        self._items = []
        return []

    # --------------------------------------------------------------------- reads

    def read(self) -> list[PlanItem]:
        """Return a copy of the current ordered list."""
        return [item.copy() for item in self._items]

    def as_dicts(self) -> list[dict[str, str]]:
        return [item.as_dict() for item in self._items]

    def has_items(self) -> bool:
        return bool(self._items)

    def counts(self) -> dict[str, int]:
        """Per-status counts plus a ``total`` key."""
        out: dict[str, int] = {s: 0 for s in sorted(VALID_STATUSES)}
        for item in self._items:
            out[item.status] = out.get(item.status, 0) + 1
        out["total"] = len(self._items)
        return out

    def format_for_injection(self) -> str | None:
        """Render the ACTIVE list for post-compaction re-injection.

        Returns a human-readable block (only pending/in_progress items), or
        ``None`` when there is nothing active to re-inject.
        """
        active = [it for it in self._items if it.status in _ACTIVE_STATUSES]
        if not active:
            return None
        lines = ["[Your active task list was preserved across context compression]"]
        for item in active[:_MAX_INJECTED_ITEMS]:
            marker = _MARKERS.get(item.status, "[?]")
            lines.append(f"- {marker} {item.id}. {item.content} ({item.status})")
        overflow = len(active) - _MAX_INJECTED_ITEMS
        if overflow > 0:
            lines.append(f"- … (+{overflow} more active items not shown)")
        return "\n".join(lines)

    def last_demoted(self) -> list[str]:
        """Ids demoted by the most recent single-in_progress auto-correction."""
        return list(self._last_demoted)

    # ----------------------------------------------------------------- internals

    def _enforce_single_in_progress(self, *, prefer_id: str | None) -> None:
        """AUTO-CORRECT the single-in_progress invariant (demote, never reject).

        If more than one item is ``in_progress``, KEEP ``prefer_id`` when it is one
        of them (the item the caller just explicitly started), otherwise keep the
        FIRST; demote the rest to ``pending``. Keeping the just-touched item is what
        lets ``set_status(id=2, in_progress)`` advance the active step instead of
        being silently undone. The demotion is recorded so the tool can note it.
        """
        self._last_demoted = []
        in_progress = [it for it in self._items if it.status == _IN_PROGRESS]
        if len(in_progress) <= 1:
            return
        keeper = next((it for it in in_progress if it.id == prefer_id), in_progress[0])
        demoted: list[str] = []
        for item in in_progress:
            if item is keeper:
                continue
            item.status = _DEFAULT_STATUS
            demoted.append(item.id)
        self._last_demoted = demoted
        log.tool.info(
            "plan_store: auto-corrected single-in_progress invariant",
            extra={"_fields": {"kept": keeper.id, "demoted": demoted, "demoted_count": len(demoted)}},
        )

    @classmethod
    def _normalise(cls, item: Mapping[str, object]) -> PlanItem:
        """Coerce a raw item into a clean :class:`PlanItem` — never raises."""
        item_id = cls._coerce_str(item.get("id")).strip() or "?"
        content = cls._coerce_str(item.get("content")).strip() or "(no description)"
        status = cls._coerce_str(item.get("status")).strip().lower()
        if status not in VALID_STATUSES:
            status = _DEFAULT_STATUS
        return PlanItem(id=item_id, content=content, status=status)

    @staticmethod
    def _coerce_str(value: object) -> str:
        if value is None:
            return ""
        return str(value)

    @classmethod
    def _dedupe_by_id(
        cls, items: Iterable[Mapping[str, object]]
    ) -> list[Mapping[str, object]]:
        """Collapse duplicate ids — last occurrence wins, earliest position kept.

        Non-mapping junk is skipped rather than raising (self-healing).
        """
        materialised = [it for it in items if isinstance(it, Mapping)]
        last_index: dict[str, int] = {}
        for i, it in enumerate(materialised):
            item_id = cls._coerce_str(it.get("id")).strip() or "?"
            last_index[item_id] = i
        # Keep the *value* from the last occurrence, ordered by first appearance.
        first_pos: dict[str, int] = {}
        for i, it in enumerate(materialised):
            item_id = cls._coerce_str(it.get("id")).strip() or "?"
            first_pos.setdefault(item_id, i)
        ordered_ids = sorted(last_index, key=lambda k: first_pos[k])
        return [materialised[last_index[item_id]] for item_id in ordered_ids]

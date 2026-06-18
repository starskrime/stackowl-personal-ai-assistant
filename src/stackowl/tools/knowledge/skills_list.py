"""skills_list — enumerate available skills (terse, token-cheap).

A skill is a *procedure* / how-to the agent can FOLLOW — not a callable tool.
This is the progressive-disclosure tier-1 read: it returns one terse line per
skill (``source:name  [category]  enabled/disabled — one-line description``) so
the agent can survey what procedures exist WITHOUT paying for any bodies. To read
a specific skill's full procedure, follow up with ``skill_view``.

Enumeration routes through :class:`SkillIndexStore`: there is no single
list-all, so we union ``store.list_for_source(source)`` across every declared
:data:`SkillSource`. An empty index yields an empty list (never an error).

Filters (impl vote #2 — exposed set):

* ``category`` — exact match on the skill's manifest ``category``/``tags`` (see
  :meth:`_category_of`). Unknown category → empty subset (not an error).
* ``disabled`` — include or exclude disabled skills. Default EXCLUDES disabled
  (the common "what can I use right now" case); pass ``disabled=true`` to include
  them.
* ``platform`` — ACCEPTED for forward-compat / port parity but a NO-OP here: the
  ``Skill`` / ``SkillManifest`` model carries no platform/os field, so there is
  nothing to filter on. We surface a one-line note rather than silently dropping
  the arg or pretending to filter (graceful omission, not a lie).

Sort (impl vote #1): by ``(category, name)`` — groups related procedures
together, stable and deterministic. This mirrors the ported tool's category-then-
name ordering.

Severity (operator decision): ``read``. ``toolset_group="knowledge"`` — the READ
knowledge group beside ``memory`` / ``skill_view``.

Provenance / port-vs-build: PORT — the category/disabled filter + category-then-
name sort + terse name/description projection are ported and re-expressed
neutrally onto our ``SkillIndexStore`` (the original walked the filesystem; we
read the index). See ``_bmad-output/research/tool-port-analysis.md`` (E4
``skills_list`` row).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, get_args

from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.skills.manifest import SkillSource
from stackowl.tools.base import Tool, ToolManifest, ToolResult

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.skills.store import Skill, SkillIndexStore

_ALL_SOURCES: tuple[SkillSource, ...] = tuple(get_args(SkillSource))
_MAX_DESC_CHARS = 120


class SkillsListTool(Tool):
    """Enumerate available skills (terse one-line projection, never bodies)."""

    @property
    def name(self) -> str:
        return "skills_list"

    @property
    def description(self) -> str:
        return (
            "Enumerate the skills (reusable procedures / how-tos) available to "
            "the agent, one terse line each (source:name, category, enabled "
            "state, one-line description) — NEVER full bodies. Filter by "
            "'category' or include disabled skills with disabled=true. A 'skill' "
            "is a procedure the agent FOLLOWS, not a callable tool. "
            "LANE: surveying WHICH procedures exist before picking one. "
            "ANTI-LANE: do NOT use this to READ one skill's steps (use "
            "skill_view); do NOT use it to find a TOOL/capability (use "
            "tool_search); do NOT use it to recall a FACT (use memory). It lists "
            "procedures, not tools and not facts."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional exact category filter (e.g. 'analysis').",
                },
                "disabled": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Include disabled skills (default false → only enabled)."
                    ),
                },
                "platform": {
                    "type": "string",
                    "description": (
                        "Accepted for compatibility but currently a no-op "
                        "(skills carry no platform field)."
                    ),
                },
            },
            "required": [],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            toolset_group="knowledge",
        )

    # ------------------------------------------------------------------ dispatch

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        category = self._opt_str(kwargs.get("category"))
        include_disabled = self._coerce_bool(kwargs.get("disabled"))
        platform = self._opt_str(kwargs.get("platform"))
        # 1. ENTRY
        log.tool.info(
            "skills_list.execute: entry",
            extra={"_fields": {
                "category": category, "disabled": include_disabled,
                "platform": platform,
            }},
        )

        # Self-healing: a missing store degrades to a structured 'unavailable'.
        store = get_services().skill_store
        if store is None:
            return self._unavailable("store", "no skill store is configured", t0)

        try:
            # 3. STEP — union every source (no list-all on the store).
            skills = await self._enumerate(store)
            # 2. DECISION — apply filters, then deterministic sort.
            filtered = self._filter(skills, category=category, include_disabled=include_disabled)
            filtered.sort(key=lambda s: (self._category_of(s) or "", s.name))
            output = self._format(filtered, platform=platform)
            # 4. EXIT
            return self._ok(
                output, t0,
                extra={"total": len(skills), "shown": len(filtered)},
            )
        except Exception as exc:  # B5 / self-healing — degrade, never raise.
            log.tool.error(
                "skills_list.execute: listing failed — degrading to structured error",
                exc_info=exc,
            )
            return self._unavailable("list", f"{type(exc).__name__}: {exc}", t0)

    # ------------------------------------------------------------------ steps

    async def _enumerate(self, store: SkillIndexStore) -> list[Skill]:
        """Union every source's skills (the store has no single list-all)."""
        out: list[Skill] = []
        for source in _ALL_SOURCES:
            out.extend(await store.list_for_source(source))
        log.tool.debug(
            "skills_list.execute: enumerated",
            extra={"_fields": {"count": len(out), "sources": len(_ALL_SOURCES)}},
        )
        return out

    def _filter(
        self, skills: list[Skill], *, category: str | None, include_disabled: bool,
    ) -> list[Skill]:
        out: list[Skill] = []
        for s in skills:
            if not include_disabled and not s.enabled:
                continue
            if category is not None and self._category_of(s) != category:
                continue
            out.append(s)
        return out

    @staticmethod
    def _category_of(skill: Skill) -> str | None:
        """Best-effort category for a skill.

        The manifest has no dedicated ``category`` field; categorization rides in
        the manifest JSON (``category``) or as the first manifest ``tag``. We read
        whatever is present and degrade to ``None`` (uncategorized) gracefully.
        """
        manifest = skill.manifest_json
        raw = manifest.get("category")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        tags = manifest.get("tags")
        if isinstance(tags, list) and tags:
            first = tags[0]
            if isinstance(first, str) and first.strip():
                return first.strip()
        return None

    def _format(self, skills: list[Skill], *, platform: str | None) -> str:
        lines: list[str] = []
        if platform is not None:
            lines.append(
                f"(note: 'platform={platform}' ignored — skills carry no platform field)",
            )
        if not skills:
            lines.append("(no skills)")
            return "\n".join(lines)
        lines.append(f"{len(skills)} skill(s):")
        for s in skills:
            cat = self._category_of(s) or "-"
            state = "enabled" if s.enabled else "disabled"
            desc = self._one_line(s.description)
            lines.append(f"  - {s.source}:{s.name}  [{cat}]  {state} — {desc}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _one_line(text: str) -> str:
        flat = " ".join((text or "").split())
        if len(flat) > _MAX_DESC_CHARS:
            return flat[: _MAX_DESC_CHARS - 1] + "…"
        return flat or "(no description)"

    @staticmethod
    def _opt_str(raw: object) -> str | None:
        if raw is None:
            return None
        s = str(raw).strip()
        return s or None

    @staticmethod
    def _coerce_bool(raw: object) -> bool:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "y", "on")
        if isinstance(raw, int):
            return raw != 0
        return False

    @staticmethod
    def _ok(
        output: str, t0: float, *, extra: dict[str, object] | None = None,
    ) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "skills_list.execute: exit",
            extra={"_fields": {"success": True, "duration_ms": duration_ms, **(extra or {})}},
        )
        return ToolResult(success=True, output=output, duration_ms=duration_ms)

    @staticmethod
    def _unavailable(source: str, reason: str, t0: float) -> ToolResult:
        """Self-healing: a down/missing store degrades to a structured (failed)
        ToolResult — never a raise. (Empty index is NOT unavailable — that is a
        successful empty list.)"""
        msg = f"skills unavailable ({source}): {reason}"
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.warning(
            "skills_list.execute: store unavailable — structured degradation",
            extra={"_fields": {"source": source, "reason": reason, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)

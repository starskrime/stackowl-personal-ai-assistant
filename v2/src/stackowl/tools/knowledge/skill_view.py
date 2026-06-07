"""skill_view — read ONE skill's full procedure by qualified name.

A skill is a *procedure* / how-to (a reusable markdown playbook the agent can
follow), NOT a callable tool. This tool resolves a skill by qualified name and
returns its body PLUS any linked reference files (``references/*.md``) subloaded
one level deep — the progressive-disclosure read counterpart to ``skills_list``
(which only enumerates).

Resolution routes through :class:`SkillIndexStore`:

* Qualified ``source:name`` (e.g. ``builtin:brainstorming``) → ``store.get(source, name)``.
* Bare ``name`` → searched across every :data:`SkillSource` (first hit wins,
  sources tried in a stable order) so the agent need not know which source a
  skill lives in.

The skill *body* and the on-disk ``references/`` directory are located via the
indexed ``Skill.path`` (the skill's directory under
``~/.stackowl/workspace/skills/<source>/<name>/``); the body itself comes from
the index (``Skill.body_text``) so a view never re-parses SKILL.md.

Reference subload is ONE LEVEL (the ``references/`` dir, non-recursive — impl
vote #1): a flat ``references/*.md`` listing is the documented skill layout, and
one level keeps the read bounded and token-cheap. Path-traversal is impossible
because we only ever glob INSIDE the resolved, validated skill dir.

Preprocess/template render (impl vote #2): this codebase ships no skill template
engine, so the raw body is returned verbatim — we do NOT invent one. If a
preprocess step is added later it slots in at :meth:`_render`.

Severity (operator decision): ``read`` — pure read, no mutation, never gated.
``toolset_group="knowledge"`` — lives in the READ knowledge group beside
``memory`` / ``skills_list``.

Provenance / port-vs-build: PORT — the qualified-name dispatch + linked-file
subload + (no-op) preprocess shape is ported and re-expressed neutrally onto our
``SkillIndexStore``; the original's plugin-manager lookups become store reads.
See ``_bmad-output/research/tool-port-analysis.md`` (E4 ``skill_view`` row).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, get_args

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.services import get_services
from stackowl.skills.manifest import SkillSource
from stackowl.skills.skill_focus import FOCUS_TRACKER
from stackowl.tools.base import Tool, ToolManifest, ToolResult

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.skills.store import Skill, SkillIndexStore

# Stable order in which bare names are probed across sources. builtin/user
# first (the human-curated, most-likely-referenced sources), then installed
# packs, then agent-authored learned skills.
_SOURCE_ORDER: tuple[SkillSource, ...] = ("builtin", "user", "installed", "learned")
# All declared sources, for the "did you mean a source:name?" hint.
_ALL_SOURCES: tuple[str, ...] = tuple(get_args(SkillSource))

_REFERENCES_DIR = "references"
_MD_SUFFIX = ".md"
# Bound the per-reference subload so a pathological file can't blow the context.
_MAX_REF_CHARS = 20_000


class SkillViewTool(Tool):
    """Read one skill's full procedure (body + linked references) by qualified name."""

    @property
    def name(self) -> str:
        return "skill_view"

    @property
    def description(self) -> str:
        return (
            "Read ONE skill's full procedure by qualified name "
            "('source:name' like 'builtin:brainstorming', or a bare 'name' "
            "searched across all sources). Returns the skill body PLUS any "
            "linked reference files (references/*.md) subloaded one level deep. "
            "A 'skill' is a reusable procedure / how-to the agent FOLLOWS — not "
            "a callable tool. "
            "LANE: pulling up the step-by-step contents of one known skill. "
            "ANTI-LANE: do NOT use this to ENUMERATE which skills exist (use "
            "skills_list); do NOT use it to find a TOOL/capability (use "
            "tool_search); do NOT use it to recall a FACT (use memory). It "
            "reads a procedure, not a tool and not a fact."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Qualified skill name 'source:name' (source ∈ "
                        f"{'|'.join(_ALL_SOURCES)}) or a bare 'name' searched "
                        "across all sources."
                    ),
                },
            },
            "required": ["name"],
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
        name = str(kwargs.get("name", "")).strip()
        # 1. ENTRY
        log.tool.info(
            "skill_view.execute: entry",
            extra={"_fields": {"name": name}},
        )

        if not name:
            return self._err("skill_view requires a non-empty 'name'.", t0)

        # Self-healing: resolve the store once; a missing store surfaces as a
        # structured 'skills unavailable', never a raise.
        store = get_services().skill_store
        if store is None:
            return self._unavailable("store", "no skill store is configured", t0)

        try:
            # 2. DECISION — qualified 'source:name' vs bare name.
            skill = await self._resolve(store, name)
            if skill is None:
                return self._err(
                    f"Skill '{name}' not found. Use skills_list to see available "
                    "skills, or qualify as 'source:name' "
                    f"(source ∈ {'|'.join(_ALL_SOURCES)}).",
                    t0,
                )
            output = self._render(skill)
            # Hysteresis: record this view so the skill stays stickier next turn.
            ctx = TraceContext.get()
            owl = ctx.get("owl_name")
            session = ctx.get("session_id")
            if owl and session:
                turn = FOCUS_TRACKER.begin_turn(owl, session)
                FOCUS_TRACKER.mark_viewed(owl, session, skill.name, turn)
            # 4. EXIT
            return self._ok(
                output, t0,
                extra={"skill": skill.name, "source": skill.source},
            )
        except Exception as exc:  # B5 / self-healing — degrade, never raise.
            log.tool.error(
                "skill_view.execute: read failed — degrading to structured error",
                exc_info=exc,
                extra={"_fields": {"name": name}},
            )
            return self._unavailable("view", f"{type(exc).__name__}: {exc}", t0)

    # ------------------------------------------------------------------ resolve

    async def _resolve(self, store: SkillIndexStore, name: str) -> Skill | None:
        """Resolve a qualified ('source:name') or bare name to one Skill.

        Qualified resolution is exact. A bare name is probed across every source
        in :data:`_SOURCE_ORDER` (first hit wins) so the caller need not know the
        owning source.
        """
        if ":" in name:
            source_raw, _, bare = name.partition(":")
            source = source_raw.strip()
            bare = bare.strip()
            if source not in _ALL_SOURCES or not bare:
                # 2. DECISION — malformed qualifier: fall back to a whole-string
                # bare lookup rather than guessing a source.
                log.tool.debug(
                    "skill_view.execute: unrecognized source qualifier — bare lookup",
                    extra={"_fields": {"source": source, "name": name}},
                )
            else:
                hit = await store.get(source, bare)  # type: ignore[arg-type]
                return hit
        # Bare name: probe each source in stable order.
        for source in _SOURCE_ORDER:
            hit = await store.get(source, name)
            if hit is not None:
                return hit
        return None

    # ------------------------------------------------------------------ render

    def _render(self, skill: Skill) -> str:
        """Render the skill body + subloaded references into one string.

        Preprocess/template render is a no-op here (no engine in this codebase —
        impl vote #2): the indexed body is returned verbatim. References are
        subloaded one level from the skill dir (impl vote #1).
        """
        header = f"# Skill: {skill.source}:{skill.name}"
        if not skill.enabled:
            header += "  (disabled)"
        parts = [header, ""]
        if skill.description:
            parts.append(skill.description)
            parts.append("")
        parts.append(skill.body_text.rstrip("\n"))

        refs = self._subload_references(skill)
        if refs:
            parts.append("")
            parts.append(f"## Linked references ({len(refs)})")
            for rel, body in refs:
                parts.append("")
                parts.append(f"### {rel}")
                parts.append(body.rstrip("\n"))
        return "\n".join(parts)

    def _subload_references(self, skill: Skill) -> list[tuple[str, str]]:
        """Read ``references/*.md`` one level deep from the skill dir.

        Returns ``(relative_path, body)`` pairs sorted by name. Missing dir → no
        refs (body-only view). Any per-file read error is logged and skipped —
        a broken reference never sinks the whole view (self-healing).
        """
        skill_dir = Path(skill.path)
        refs_dir = skill_dir / _REFERENCES_DIR
        if not refs_dir.is_dir():
            return []
        # Defense-in-depth: this is the one place that reads the references subtree
        # (the write-time security scan does not cover it), so enforce confinement
        # HERE rather than inheriting it — a reference that resolves (through a
        # symlink) outside the skill dir is never read.
        try:
            base = skill_dir.resolve()
        except OSError:
            return []
        out: list[tuple[str, str]] = []
        try:
            candidates = sorted(refs_dir.glob(f"*{_MD_SUFFIX}"))
        except OSError as exc:
            log.tool.warning(
                "skill_view.execute: references glob failed — body only",
                exc_info=exc,
                extra={"_fields": {"skill": skill.name, "dir": str(refs_dir)}},
            )
            return []
        for ref in candidates:
            try:
                real = ref.resolve()
            except OSError:
                continue
            if not real.is_relative_to(base) or not real.is_file():
                log.tool.warning(
                    "skill_view.execute: reference escapes skill dir — skipping",
                    extra={"_fields": {"skill": skill.name, "ref": ref.name}},
                )
                continue
            try:
                body = ref.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                log.tool.warning(
                    "skill_view.execute: reference read failed — skipping",
                    exc_info=exc,
                    extra={"_fields": {"skill": skill.name, "ref": ref.name}},
                )
                continue
            if len(body) > _MAX_REF_CHARS:
                body = body[:_MAX_REF_CHARS] + "\n…(reference truncated)"
            rel = f"{_REFERENCES_DIR}/{ref.name}"
            out.append((rel, body))
        # 3. STEP
        log.tool.debug(
            "skill_view.execute: subloaded references",
            extra={"_fields": {"skill": skill.name, "n_refs": len(out)}},
        )
        return out

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _ok(
        output: str, t0: float, *, extra: dict[str, object] | None = None,
    ) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "skill_view.execute: exit",
            extra={"_fields": {"success": True, "duration_ms": duration_ms, **(extra or {})}},
        )
        return ToolResult(success=True, output=output, duration_ms=duration_ms)

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "skill_view.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)

    @staticmethod
    def _unavailable(source: str, reason: str, t0: float) -> ToolResult:
        """Self-healing: a down/missing store degrades to a structured (failed)
        ToolResult — never a raise."""
        msg = f"skills unavailable ({source}): {reason}"
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.warning(
            "skill_view.execute: store unavailable — structured degradation",
            extra={"_fields": {"source": source, "reason": reason, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)

"""memory — durable semantic FACTS across sessions (add/search/get/forget).

A single action-dispatching tool over the existing tri-store memory substrate
(``MemoryBridge``: LanceDB vectors + Kuzu graph + SQLite FTS5). It is a thin
wrapper: every write routes through the shared provenance chokepoints in
:mod:`stackowl.commands.memory_helpers` (``remember_fact`` / ``forget_fact``) so
the slash command and this tool share ONE code path, and reads route through
``MemoryBridge.recall`` so cross-source vector+FTS hybrid recall is handled by
the bridge — NO Python-side aggregation glue ([[feedback_use_existing_infrastructure]]).

Provenance (E4 design change #3): tool-authored ``add`` facts are tagged
``source_type="agent_self"`` so self-authored content is distinguishable from
human-authored (``manual``) facts for future recall down-ranking and
privileged-context exclusion.

Severity (operator decision): ``write`` — memory mutation is write+audit, but
it is frequent and low-blast-radius (an ``agent_self`` fact is undoable via
``forget`` and is audited), NOT ``consequential``. ``toolset_group="knowledge"``
(operator decision): memory lives in the READ knowledge group; its writes are
audited/undoable, not consent-gated.

Cron default-deny note: unlike ``skill_manage``, ``memory`` does NOT carry a
non-interactive hard-deny. There is no clean per-call interactive signal to key
it off, and a blanket deny would block every legitimate write. Memory is
low-blast-radius and every mutation is audited + tagged ``agent_self`` — that
provenance trail is the control here, not a gate.

Provenance / port-vs-build: BUILD (the reference agent's flat-file memory is
incompatible with our tri-store; porting would create a second store). See
``_bmad-output/research/tool-port-analysis.md`` (E4 ``memory`` row).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, cast

from stackowl.commands.memory_helpers import forget_fact, remember_fact
from stackowl.infra.observability import log
from stackowl.memory.fact_promoter import FactPromoter
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.knowledge.guards import AGENT_SELF_SOURCE_TYPE

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.commands.memory_helpers import RememberSourceType
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.memory.models import MemoryRecord, StagedFact

_VALID_ACTIONS: tuple[str, ...] = ("add", "search", "get", "forget")
_DEFAULT_LIMIT = 5
_MAX_LIMIT = 50
_ACTOR = "agent_self:memory"
_SOURCE_REF = "tool:memory"


def _did_you_mean(action: str) -> str:
    """Render a structured 'did you mean' for an unknown action enum value."""
    valid = "|".join(_VALID_ACTIONS)
    # Cheapest useful suggestion: a valid action sharing the first char.
    suggestion = next((a for a in _VALID_ACTIONS if action and a[0] == action[0]), None)
    hint = f" Did you mean '{suggestion}'?" if suggestion else ""
    return f"Unknown action {action!r}. Valid actions: {valid}.{hint}"


class MemoryTool(Tool):
    """Durable semantic-fact store: add/search/get/forget across sessions."""

    @property
    def name(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return (
            "Durable semantic FACTS that persist across sessions. "
            "action='add' remembers a fact (tagged as agent-authored, audited, "
            "undoable via forget); action='search' recalls facts by meaning "
            "(hybrid vector+keyword); action='get' fetches a fact by id (or id "
            "prefix); action='forget' deletes a fact by id. "
            "LANE: long-lived knowledge ('the user prefers tabs', 'the prod DB "
            "is in eu-west-1'). "
            "ANTI-LANE: do NOT use memory to find what was literally SAID in a "
            "past conversation — use session_search for that. Do NOT use it to "
            "read a procedure or how-to — use skill_view for that."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_VALID_ACTIONS),
                    "description": "add | search | get | forget",
                },
                "content": {
                    "type": "string",
                    "description": "The fact text to remember (action='add').",
                },
                "query": {
                    "type": "string",
                    "description": "Search query for recall (action='search').",
                },
                "fact_id": {
                    "type": "string",
                    "description": "Fact id or id prefix (action='get' / 'forget').",
                },
                "limit": {
                    "type": "integer",
                    "default": _DEFAULT_LIMIT,
                    "description": f"Max hits for search (1-{_MAX_LIMIT}).",
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
            action_severity="write",
            toolset_group="knowledge",
        )

    # ------------------------------------------------------------------ dispatch

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        action = str(kwargs.get("action", "")).strip().lower()
        # 1. ENTRY
        log.tool.info(
            "memory.execute: entry",
            extra={"_fields": {"action": action}},
        )

        # Hard-validate the action enum with a structured 'did you mean' — never
        # a stack trace, never a silent default to one of the branches.
        if action not in _VALID_ACTIONS:
            return self._err(_did_you_mean(action), t0)

        # Self-healing: resolve the bridge once; a missing/None bridge surfaces
        # as a structured 'memory unavailable', never a raise.
        bridge = get_services().memory_bridge
        if bridge is None:
            return self._unavailable("bridge", "no memory bridge is configured", t0)

        try:
            # 2. DECISION — dispatch by validated action.
            if action == "add":
                return await self._add(bridge, kwargs, t0)
            if action == "search":
                return await self._search(bridge, kwargs, t0)
            if action == "get":
                return await self._get(bridge, kwargs, t0)
            return await self._forget(bridge, kwargs, t0)
        except Exception as exc:  # B5 / self-healing — degrade, never raise.
            log.tool.error(
                "memory.execute: action failed — degrading to structured error",
                exc_info=exc,
                extra={"_fields": {"action": action}},
            )
            return self._unavailable(
                action, f"{type(exc).__name__}: {exc}", t0,
            )

    # ------------------------------------------------------------------ actions

    async def _add(
        self, bridge: MemoryBridge, kwargs: dict[str, object], t0: float,
    ) -> ToolResult:
        content = str(kwargs.get("content", "")).strip()
        if not content:
            return self._err("action='add' requires 'content'.", t0)

        # The FactPromoter needs a DbPool; both ride the ambient services.
        services = get_services()
        db = services.db_pool
        if db is None:
            return self._unavailable("add", "no database pool is configured", t0)

        # Wire the LanceDB adapter (owned by the bridge) into the promoter so the
        # committed fact's vector is upserted for SEMANTIC recall; falls back to
        # None for bridges that don't expose one (FTS recall still works).
        lancedb = getattr(bridge, "lancedb", None)
        promoter = FactPromoter(db, lancedb=lancedb)
        # Route through the shared chokepoint — agent_self provenance + audit.
        # Pass the embedding registry so the fact is embedded at remember time.
        fact_id = await remember_fact(
            bridge,
            promoter,
            content,
            source_type=cast("RememberSourceType", AGENT_SELF_SOURCE_TYPE),
            source_ref=_SOURCE_REF,
            audit=services.audit_logger,
            actor=_ACTOR,
            embedding_registry=services.embedding_registry,
        )
        # 4. EXIT — mutating turns must be VISIBLE (party #7): state plainly what
        # was remembered, with the id so it can be forgotten later.
        msg = f"Remembered [{fact_id}]: {content}"
        return self._ok(msg, t0, extra={"fact_id": fact_id})

    async def _search(
        self, bridge: MemoryBridge, kwargs: dict[str, object], t0: float,
    ) -> ToolResult:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return self._err("action='search' requires 'query'.", t0)
        limit = self._coerce_limit(kwargs.get("limit"))
        # Hybrid vector+FTS recall is the bridge's job — no glue here.
        hits = await bridge.recall(query, limit=limit)
        return self._ok(self._format_hits(hits), t0, extra={"hits": len(hits)})

    async def _all_prefix_matches(self, bridge: MemoryBridge, prefix: str) -> list[StagedFact]:
        """All facts whose id starts with ``prefix``, de-duplicated by fact_id.

        Unlike the slash path's first-match resolver, this surfaces ALL matches so
        the tool can refuse an ambiguous id instead of acting on an arbitrary one.
        """
        seen: dict[str, StagedFact] = {}
        for status in ("staged", "committed", "rejected"):
            try:
                facts = await bridge.list_staged(status=status)
            except Exception as exc:
                log.tool.warning(
                    "memory.execute: list_staged failed",
                    exc_info=exc, extra={"_fields": {"status": status}},
                )
                continue
            for f in facts:
                if f.fact_id.startswith(prefix) and f.fact_id not in seen:
                    seen[f.fact_id] = f
        return list(seen.values())

    async def _resolve_unique(
        self, bridge: MemoryBridge, fact_id: str, t0: float, *, verb: str,
    ) -> StagedFact | ToolResult:
        """Resolve ``fact_id`` to exactly one fact, or a structured refusal/miss.

        Exact full-id wins; a prefix matching >1 fact is REFUSED rather than acting
        on an arbitrary one (M1); 0 matches is a structured miss.
        """
        matches = await self._all_prefix_matches(bridge, fact_id)
        exact = next((f for f in matches if f.fact_id == fact_id), None)
        if exact is not None:
            return exact
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            heads = ", ".join(m.fact_id[:12] for m in matches[:5])
            return self._ok(
                f"(ambiguous id '{fact_id}' matches {len(matches)} facts: {heads}… — "
                f"use a longer/exact id to {verb})",
                t0, extra={"ambiguous": True, "match_count": len(matches)},
            )
        return self._ok(f"(no fact matches id '{fact_id}')", t0, extra={"found": False})

    async def _get(
        self, bridge: MemoryBridge, kwargs: dict[str, object], t0: float,
    ) -> ToolResult:
        fact_id = str(kwargs.get("fact_id", "")).strip()
        if not fact_id:
            return self._err("action='get' requires 'fact_id'.", t0)
        resolved = await self._resolve_unique(bridge, fact_id, t0, verb="view it")
        if isinstance(resolved, ToolResult):
            return resolved
        return self._ok(
            f"[{resolved.fact_id}] ({resolved.source_type}) {resolved.content}",
            t0,
            extra={"found": True, "fact_id": resolved.fact_id},
        )

    async def _forget(
        self, bridge: MemoryBridge, kwargs: dict[str, object], t0: float,
    ) -> ToolResult:
        fact_id = str(kwargs.get("fact_id", "")).strip()
        if not fact_id:
            return self._err("action='forget' requires 'fact_id'.", t0)
        resolved = await self._resolve_unique(bridge, fact_id, t0, verb="forget it")
        if isinstance(resolved, ToolResult):
            return resolved  # ambiguous (refused) or structured no-op miss
        # Provenance guard (M1): the agent's memory tool may only forget facts IT
        # authored (agent_self). Human-authored memory is never erased by the
        # agent — that requires the user via /memory forget.
        if resolved.source_type != AGENT_SELF_SOURCE_TYPE:
            return self._err(
                f"Refusing to forget [{resolved.fact_id}]: it is a '{resolved.source_type}' "
                "fact (not authored by the agent). Human-authored memory can only be removed "
                "by the user via /memory forget.",
                t0,
            )
        await forget_fact(
            bridge, resolved.fact_id, audit=get_services().audit_logger, actor=_ACTOR,
        )
        # Mutating turn — make the deletion visible.
        return self._ok(
            f"Forgot [{resolved.fact_id}]: {resolved.content}",
            t0,
            extra={"forgotten": True, "fact_id": resolved.fact_id},
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _coerce_limit(raw: object) -> int:
        limit = _DEFAULT_LIMIT
        if isinstance(raw, bool):
            return _DEFAULT_LIMIT
        if isinstance(raw, int):
            limit = raw
        elif isinstance(raw, str) and raw.strip().isdigit():
            limit = int(raw.strip())
        return max(1, min(limit, _MAX_LIMIT))

    @staticmethod
    def _format_hits(hits: list[MemoryRecord]) -> str:
        if not hits:
            return "(no matches)"
        lines = [f"{len(hits)} match(es):"]
        for h in hits:
            snippet = h.content if len(h.content) <= 200 else h.content[:197] + "..."
            lines.append(f"  - [{h.fact_id}] ({h.source_type}) {snippet}")
        return "\n".join(lines)

    @staticmethod
    def _ok(
        output: str, t0: float, *, extra: dict[str, object] | None = None,
    ) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "memory.execute: exit",
            extra={"_fields": {"success": True, "duration_ms": duration_ms, **(extra or {})}},
        )
        return ToolResult(success=True, output=output, duration_ms=duration_ms)

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "memory.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)

    @staticmethod
    def _unavailable(source: str, reason: str, t0: float) -> ToolResult:
        """Self-healing: a down/missing store degrades to a structured result.

        Surfaced as a FAILED ToolResult (so the model knows the write did not
        land) but NEVER as a raise — the pipeline keeps running.
        """
        msg = f"memory unavailable ({source}): {reason}"
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.warning(
            "memory.execute: store unavailable — structured degradation",
            extra={"_fields": {"source": source, "reason": reason, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)

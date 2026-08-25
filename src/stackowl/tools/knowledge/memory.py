"""memory — the agent's curated memory, plus search over the archive (D08.1).

WRITES GO TO CURATED FILES, not to the fact store. ``add``/``replace``/``remove``
operate on ``~/.stackowl/memory/USER.md`` (the user profile) and
``<owl>.md`` (that owl's own notes), against a hard character budget the agent
must consolidate within. ``search``/``get`` still read the archive.

Why the write path moved: measured 2026-08-08, the fact store had grown to
88,631 entries of which 37.1% mentioned a trace id or ``failure_class`` — the
platform's own diagnostics stored as durable memory about the user — and its
most-reinforced entry was a three-week-stale date. An unbounded write path with
no notion of what deserves keeping produces exactly that. The budget is the fix,
and it only works if the agent feels it, which means writing through this tool.

ONE TOOL, not two (D08.1 R2Q5). A second memory tool is the fork dedup target
X4 forbids, and the model already knows this one.

Search remains a single action-dispatching read over the tri-store substrate
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
from typing import TYPE_CHECKING

from stackowl.commands.memory_helpers import forget_fact
from stackowl.infra.observability import log
from stackowl.memory.curated import DURABILITIES, CuratedMemory, note_write
from stackowl.memory.trust import render_at_trust
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.knowledge.guards import AGENT_SELF_SOURCE_TYPE
from stackowl.tools.knowledge.skill_validation import scan_text

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.memory.models import MemoryRecord, StagedFact

_VALID_ACTIONS: tuple[str, ...] = (
    "add", "replace", "remove", "search", "get", "forget",
)
#: Writes default to the user profile. An owl writing its own notes must say so,
#: because "what I learned about my job" is the rarer, more deliberate case.
_DEFAULT_TARGET = "user"
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
                    "description": (
                        "add | replace | remove (curated memory) · "
                        "search | get | forget (the archive)"
                    ),
                },
                "target": {
                    "type": "string",
                    "description": (
                        "'user' = durable facts about the PERSON (preferences, "
                        "how they want you to work). It is shared with EVERY owl "
                        "and is the smallest budget, so keep it to a handful of "
                        "lasting facts. An owl's name = that owl's own working "
                        "notes. Job config, credentials, schedules, and logs of "
                        "what you already delivered are NOT user preferences — "
                        "put them on the owl that owns the work, or nowhere."
                    ),
                    "default": _DEFAULT_TARGET,
                },
                "durability": {
                    "type": "string",
                    "enum": list(DURABILITIES),
                    "description": (
                        "Required for add/replace. 'permanent' = true "
                        "indefinitely. 'until_changed' = true until the user "
                        "changes it. There is NO 'transient': if it will stop "
                        "being true — a date, a price, today's news — do not "
                        "remember it."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "The text to remember (add), its replacement (replace), "
                        "or the text of the entry to drop (remove)."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Search query (search), or the text of the entry being "
                        "replaced (replace)."
                    ),
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
            commit_coupling="transactional",
            toolset_group="knowledge",
            progress_key="SAVE_MEMORY",
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
            if action == "replace":
                return await self._replace(bridge, kwargs, t0)
            if action == "remove":
                return await self._remove(bridge, kwargs, t0)
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

    # ---- curated writes (D08.1) ------------------------------------------
    #
    # These three do NOT touch the fact store. They edit the two curated files,
    # which is what the system prompt actually carries.

    def _curated(self) -> CuratedMemory:
        return CuratedMemory()

    def _scanned(self, content: str, t0: float) -> ToolResult | None:
        """Refuse content a static scan calls dangerous. ``None`` means allowed.

        Memory entries land in the SYSTEM PROMPT, which makes them a
        prompt-injection surface every bit as real as a skill body — so they go
        through the same scanner, not a second one that could drift from it
        (D08.1 R3Q11). Built here rather than deferred to D08.4: this tool is
        what CREATES the writable surface.

        Fails CLOSED, matching the skill gate: a scanner that itself errors
        blocks the write, because a broken scanner must never become a bypass.
        """
        try:
            findings = scan_text(content, label="memory")
        except Exception as exc:  # B5 — fail closed
            log.tool.error(
                "memory: content scan crashed — refusing the write",
                exc_info=exc,
            )
            return self._err(
                "Security scan failed to run; refusing the write to fail closed.", t0,
            )
        blocking = [f for f in findings if f.severity in ("critical", "high")]
        if blocking:
            log.security.warning(
                "memory: refusing content flagged by the scanner",
                extra={"_fields": {
                    "patterns": sorted({f.pattern_id for f in blocking})[:5],
                }},
            )
            return self._err(
                "Refusing to remember this: the content matched "
                f"{', '.join(sorted({f.pattern_id for f in blocking})[:3])}. "
                "Memory is injected into the system prompt, so it is held to the "
                "same standard as skill content.",
                t0,
            )
        return None

    def _target(self, kwargs: dict[str, object], text: str = "") -> str:
        """Where this write goes (ESC-48).

        An EXPLICIT target always wins — inference fills a gap, it never
        overrides an instruction. With none given, the destination is inferred
        from the fact's own words against the live roster of owls, falling back
        to the user. Bakir, 2026-08-24: "Infer from the fact text" and, when it
        is unclear, "default user, always name it".

        The naming half is not decoration. Inference is only safe because every
        confirmation now states the destination, so a wrong guess is visible
        immediately instead of being answered with a bare "Saved."
        """
        explicit = str(kwargs.get("target") or "").strip()
        if explicit:
            return explicit
        if not text:
            return _DEFAULT_TARGET
        try:
            return self._curated().infer_target(text)
        except Exception as exc:  # B5 — a routing guess must never cost the write
            log.memory.warning(
                "[tools] memory._target: inference failed — using the user file",
                exc_info=exc,
            )
            return _DEFAULT_TARGET

    async def _add(
        self, bridge: MemoryBridge, kwargs: dict[str, object], t0: float,
    ) -> ToolResult:
        content = str(kwargs.get("content", "")).strip()
        if not content:
            return self._err("action='add' requires 'content'.", t0)
        durability = str(kwargs.get("durability", "")).strip()
        if durability not in DURABILITIES:
            return self._err(
                f"action='add' requires durability={'|'.join(DURABILITIES)}. "
                f"There is deliberately no 'transient' — if this will stop being "
                f"true (a date, a price, today's news), it does not belong in "
                f"memory at all.",
                t0,
            )
        refusal = self._scanned(content, t0)
        if refusal is not None:
            return refusal

        result = self._curated().add(self._target(kwargs, content), content, durability)
        return self._from_curated(result, t0)

    async def _replace(
        self, bridge: MemoryBridge, kwargs: dict[str, object], t0: float,
    ) -> ToolResult:
        """Merge two entries into one. The verb consolidation-under-budget needs."""
        old = str(kwargs.get("query") or kwargs.get("fact_id") or "").strip()
        content = str(kwargs.get("content", "")).strip()
        if not old or not content:
            return self._err(
                "action='replace' requires 'query' (text of the entry to replace) "
                "and 'content' (its replacement).",
                t0,
            )
        durability = str(kwargs.get("durability", "")).strip()
        if durability not in DURABILITIES:
            return self._err(
                f"action='replace' requires durability={'|'.join(DURABILITIES)}.", t0,
            )
        refusal = self._scanned(content, t0)
        if refusal is not None:
            return refusal
        result = self._curated().replace(
            self._target(kwargs, content), old, content, durability
        )
        return self._from_curated(result, t0)

    async def _remove(
        self, bridge: MemoryBridge, kwargs: dict[str, object], t0: float,
    ) -> ToolResult:
        text = str(kwargs.get("content") or kwargs.get("query") or "").strip()
        if not text:
            return self._err(
                "action='remove' requires 'content' — the text of the entry to drop.",
                t0,
            )
        result = self._curated().remove(self._target(kwargs, text), text)
        return self._from_curated(result, t0)

    def _from_curated(self, result: object, t0: float) -> ToolResult:
        """Render a CuratedMemory result as a ToolResult, keeping its structure.

        The over-capacity refusal is NOT an error in the tool-failure sense — it
        is an instruction the model is expected to act on this turn — so it
        carries the entry list and the usage figure through rather than being
        flattened to a message.
        """
        payload = result.as_dict()  # type: ignore[attr-defined]
        if result.ok:  # type: ignore[attr-defined]
            # The agent just wrote something, so it does not need reminding.
            # Reset here rather than in CuratedMemory: the counter is per LANE
            # and only the tool call knows which lane it is on.
            session_key = getattr(get_services(), "session_key", None)
            if session_key:
                note_write(str(session_key))
            return self._ok(
                f"{result.message} ({result.usage})",  # type: ignore[attr-defined]
                t0, extra=payload,
            )
        return self._err(result.message, t0, extra=payload)  # type: ignore[attr-defined]

    async def _search(
        self, bridge: MemoryBridge, kwargs: dict[str, object], t0: float,
    ) -> ToolResult:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return self._err("action='search' requires 'query'.", t0)
        limit = self._coerce_limit(kwargs.get("limit"))
        # Hybrid vector+FTS recall over the archive is the bridge's job.
        hits = await bridge.recall(query, limit=limit)

        # SEARCH SPANS BOTH SURFACES (D08.1 R3Q10). "What do I know about X?" is
        # one question, and answering it from only half the memory would make the
        # curated entries — the ones actually in the prompt — the hardest to find.
        # Substring, not ranked: the curated files are a few dozen short lines,
        # so scoring them against BM25+cosine hits would be precision theatre.
        curated = self._curated().search(query)

        body = self._format_hits(hits)
        if curated:
            body = (
                "From curated memory (in the system prompt):\n"
                + "\n".join(f"  [{t}] {text}" for t, text in curated)
                + ("\n\nFrom the archive:\n" + body if hits else "")
            )
        return self._ok(
            body, t0, extra={"hits": len(hits), "curated_hits": len(curated)},
        )

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
        # ESC-6 — this render reaches the MODEL, and `list_staged` filters on status
        # only, so an id-prefix lookup can surface a `webpage` row. Content is framed
        # at its own trust tier by the one rule in memory/trust.py.
        return self._ok(
            f"[{resolved.fact_id}] ({resolved.source_type}) "
            + render_at_trust(
                resolved.content,
                source_type=resolved.source_type,
                trust=resolved.trust,
            ),
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
            f"Forgot [{resolved.fact_id}]: "
            + render_at_trust(
                resolved.content,
                source_type=resolved.source_type,
                trust=resolved.trust,
            ),
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
            # ESC-6 — the cap is applied BY the renderer, so truncation cannot slice
            # a fence tag in half; and every hit is framed at its own trust tier.
            lines.append(
                f"  - [{h.fact_id}] ({h.source_type}) "
                + render_at_trust(
                    h.content, source_type=h.source_type, trust=h.trust, cap=200,
                )
            )
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
    def _err(
        msg: str, t0: float, extra: dict[str, object] | None = None,
    ) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "memory.execute: exit",
            extra={"_fields": {
                "success": False, "error": msg, "duration_ms": duration_ms,
                **(extra or {}),
            }},
        )
        # Pre-execution refusal (bad/missing args) — nothing was written. Mark it as
        # no side effect so a malformed call does not trip the honest give-up floor.
        return ToolResult(
            success=False, output="", error=msg, duration_ms=duration_ms,
            side_effect_committed=False,
        )

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
        # The store was never reached — no write was attempted. No side effect, so
        # this degradation must not be counted as an unachieved consequential give-up.
        return ToolResult(
            success=False, output="", error=msg, duration_ms=duration_ms,
            side_effect_committed=False,
        )

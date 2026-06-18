"""session_search — find/replay what was LITERALLY SAID in past session turns.

Read-only recall over the canonical conversation store (``messages`` +
``conversations``, migration 0002 — the SAME SQLite tables the fact-extraction
handler already reads). Exposes three recall SHAPES via a ``mode`` arg:

* ``browse``   — paginate a session's turns in order (LIMIT + offset).
* ``discover`` — find turns whose text matches a query (substring search).
* ``scroll``   — return a small window of turns AROUND an anchor message id.

STORAGE NOTE (story-premise divergence): the E4-S5 story assumed an FTS5 index
over session messages. There is NONE in this codebase — the only FTS5 virtual
table is ``committed_facts_fts`` (durable memory facts, migration 0014), not raw
conversation turns. Rather than stand up a SECOND store (which would violate the
self-hosted single-substrate boundary), ``discover`` searches the existing
``messages.content`` column with a SQL ``LIKE`` (case-insensitive substring).
This is correct for verbatim recall and stays on the one canonical store; if an
FTS5 mirror over messages is added later it slots in behind ``_discover`` with
no contract change.

Security (shared with ``transcripts`` via :mod:`session_access`):

* REDACTION — every returned turn's text is passed through
  :func:`redact_secrets`, so a secret a user once pasted into a turn is masked
  before it re-enters the model's context. Applied even to partial results.
* VISIBILITY GUARD — :func:`resolve_visibility` limits cross-session access:
  by default the caller's CURRENT session; a different ``session_id`` is allowed
  only if it shares the same owner (``owl_name``). Cross-owner reads are refused.

Operations: every shape is LIMIT-bounded and the scroll radius is hard-capped,
so neither a long deployment's history nor a hostile ``radius`` can blow the
context window.

Severity ``read`` (pure read, never gated); ``toolset_group="knowledge"``
(reads live in the knowledge group beside ``memory`` / ``skill_view``).

Provenance / port-vs-build: HYBRID — the three-shape recall surface +
current-session exclusion are ported and re-expressed neutrally onto our SQLite
store; the value-level redactor + owner-scoped guard are built for this
codebase. See ``_bmad-output/research/tool-port-analysis.md``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.knowledge.session_access import (
    VisibilityDecision,
    redact_secrets,
    resolve_visibility,
)

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.db.pool import DbPool

_VALID_MODES: tuple[str, ...] = ("browse", "discover", "scroll")

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100
_DEFAULT_RADIUS = 3
_MAX_RADIUS = 25  # hard cap on the scroll window half-width (party: cap the anchor window)

# Ordered turns for a session. created_at then id keeps ties deterministic.
_BROWSE_SQL = """
SELECT m.id, m.role, m.content, m.created_at
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
WHERE c.session_id = ?
ORDER BY m.created_at ASC, m.id ASC
LIMIT ? OFFSET ?
"""

_DISCOVER_SQL = """
SELECT m.id, m.role, m.content, m.created_at
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
WHERE c.session_id = ? AND m.content LIKE ? ESCAPE '\\'
ORDER BY m.created_at ASC, m.id ASC
LIMIT ?
"""

# All ordered turns of a session (no LIMIT) — used to locate an anchor's index
# for scroll. Bounded by the per-session turn count, then sliced to the window.
_ORDERED_SQL = """
SELECT m.id, m.role, m.content, m.created_at
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
WHERE c.session_id = ?
ORDER BY m.created_at ASC, m.id ASC
"""

# Locate which session an anchor message belongs to (so scroll can scope itself).
_ANCHOR_SESSION_SQL = """
SELECT c.session_id
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
WHERE m.id = ?
LIMIT 1
"""


class SessionSearchTool(Tool):
    """Recall verbatim past conversation turns (browse / discover / scroll)."""

    @property
    def name(self) -> str:
        return "session_search"

    @property
    def description(self) -> str:
        return (
            "Find or replay what was LITERALLY SAID in past conversation turns "
            "(verbatim recall). mode='browse' paginates a session's turns in "
            "order; mode='discover' finds turns whose text matches a query; "
            "mode='scroll' returns the turns around an anchor message id. "
            "By default reads the CURRENT session; pass session_id to read "
            "another session of the SAME owner (cross-owner reads are refused). "
            "Secrets in returned turns are masked. "
            "LANE: recalling the exact words of a prior conversation ('what did "
            "I say about the deploy yesterday'). "
            "ANTI-LANE: do NOT use it for durable knowledge ('the user prefers "
            "tabs') — use memory; do NOT use it to read a procedure/how-to — use "
            "skill_view."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": list(_VALID_MODES),
                    "description": "browse | discover | scroll",
                },
                "session_id": {
                    "type": "string",
                    "description": (
                        "Session to read (browse/discover). Defaults to the "
                        "current session; another session must share the owner."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "Substring to find in turns (mode='discover').",
                },
                "anchor_id": {
                    "type": "string",
                    "description": "Message id to scroll around (mode='scroll').",
                },
                "limit": {
                    "type": "integer",
                    "default": _DEFAULT_LIMIT,
                    "description": f"Max turns for browse/discover (1-{_MAX_LIMIT}).",
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "description": "Pagination offset for mode='browse'.",
                },
                "radius": {
                    "type": "integer",
                    "default": _DEFAULT_RADIUS,
                    "description": f"Turns each side of the anchor (capped at {_MAX_RADIUS}).",
                },
            },
            "required": ["mode"],
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
        mode = str(kwargs.get("mode", "")).strip().lower()
        # 1. ENTRY
        log.tool.info("session_search.execute: entry", extra={"_fields": {"mode": mode}})

        if mode not in _VALID_MODES:
            valid = "|".join(_VALID_MODES)
            return self._err(f"Unknown mode {mode!r}. Valid modes: {valid}.", t0)

        db = get_services().db_pool
        if db is None:
            return self._unavailable("no database pool is configured", t0)

        try:
            # 2. DECISION — dispatch by validated mode.
            if mode == "discover":
                return await self._discover(db, kwargs, t0)
            if mode == "scroll":
                return await self._scroll(db, kwargs, t0)
            return await self._browse(db, kwargs, t0)
        except Exception as exc:  # self-healing — degrade, never raise.
            log.tool.error(
                "session_search.execute: failed — degrading to structured error",
                exc_info=exc,
                extra={"_fields": {"mode": mode}},
            )
            return self._unavailable(f"{type(exc).__name__}: {exc}", t0)

    # -------------------------------------------------------------------- shapes

    async def _browse(self, db: DbPool, kwargs: dict[str, object], t0: float) -> ToolResult:
        vis = await self._guard(db, kwargs.get("session_id"))
        if not vis.allowed:
            return self._err(vis.reason, t0)
        limit = self._coerce(kwargs.get("limit"), _DEFAULT_LIMIT, _MAX_LIMIT)
        offset = max(0, self._coerce(kwargs.get("offset"), 0, 10**9, lo=0))
        rows = await db.fetch_all(_BROWSE_SQL, (vis.session_id, limit, offset))
        # 3. STEP
        log.tool.debug(
            "session_search.execute: browse fetched",
            extra={"_fields": {"rows": len(rows), "offset": offset}},
        )
        return self._ok(self._render(rows, header=f"session {vis.session_id}"), t0, len(rows))

    async def _discover(self, db: DbPool, kwargs: dict[str, object], t0: float) -> ToolResult:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return self._err("mode='discover' requires a non-empty 'query'.", t0)
        vis = await self._guard(db, kwargs.get("session_id"))
        if not vis.allowed:
            return self._err(vis.reason, t0)
        limit = self._coerce(kwargs.get("limit"), _DEFAULT_LIMIT, _MAX_LIMIT)
        like = f"%{self._escape_like(query)}%"
        rows = await db.fetch_all(_DISCOVER_SQL, (vis.session_id, like, limit))
        log.tool.debug(
            "session_search.execute: discover matched",
            extra={"_fields": {"matches": len(rows)}},
        )
        header = f"{len(rows)} match(es) for {query!r} in session {vis.session_id}"
        return self._ok(self._render(rows, header=header), t0, len(rows))

    async def _scroll(self, db: DbPool, kwargs: dict[str, object], t0: float) -> ToolResult:
        anchor_id = str(kwargs.get("anchor_id", "")).strip()
        if not anchor_id:
            return self._err("mode='scroll' requires an 'anchor_id'.", t0)
        # The anchor's session governs the read; the visibility guard then checks it.
        anchor_rows = await db.fetch_all(_ANCHOR_SESSION_SQL, (anchor_id,))
        if not anchor_rows:
            return self._err(f"no message found with id '{anchor_id}'.", t0)
        anchor_session = anchor_rows[0].get("session_id")
        vis = await self._guard(db, anchor_session)
        if not vis.allowed:
            return self._err(vis.reason, t0)
        radius = self._coerce(kwargs.get("radius"), _DEFAULT_RADIUS, _MAX_RADIUS)

        ordered = await db.fetch_all(_ORDERED_SQL, (vis.session_id,))
        idx = next((i for i, r in enumerate(ordered) if r.get("id") == anchor_id), None)
        if idx is None:  # pragma: no cover — anchor located above; defensive
            return self._err(f"anchor '{anchor_id}' not in session.", t0)
        lo = max(0, idx - radius)
        hi = min(len(ordered), idx + radius + 1)
        window = ordered[lo:hi]
        log.tool.debug(
            "session_search.execute: scroll window",
            extra={"_fields": {"idx": idx, "radius": radius, "window": len(window)}},
        )
        header = f"turns around {anchor_id} (±{radius}) in session {vis.session_id}"
        return self._ok(self._render(window, header=header), t0, len(window))

    # -------------------------------------------------------------------- guard

    async def _guard(self, db: DbPool, session_id: object) -> VisibilityDecision:
        sid = str(session_id).strip() if isinstance(session_id, str) else None
        return await resolve_visibility(db, sid)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _escape_like(value: str) -> str:
        """Escape LIKE wildcards so a query of ``%`` matches a literal percent."""
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _coerce(raw: object, default: int, hi: int, *, lo: int = 1) -> int:
        val = default
        if isinstance(raw, bool):
            return default
        if isinstance(raw, int):
            val = raw
        elif isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
            val = int(raw.strip())
        return max(lo, min(val, hi))

    @staticmethod
    def _render(rows: list[dict[str, object]], *, header: str) -> str:
        """Render turns with REDACTION applied to every turn's content."""
        if not rows:
            return f"{header}\n(no turns)"
        lines = [header]
        for r in rows:
            role = str(r.get("role", "?"))
            content = redact_secrets(str(r.get("content", "")))
            mid = str(r.get("id", ""))[:8]
            lines.append(f"  [{mid}] {role}: {content}")
        return "\n".join(lines)

    def _ok(self, output: str, t0: float, count: int) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "session_search.execute: exit",
            extra={"_fields": {"success": True, "turns": count, "duration_ms": duration_ms}},
        )
        return ToolResult(success=True, output=output, duration_ms=duration_ms)

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "session_search.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)

    @staticmethod
    def _unavailable(reason: str, t0: float) -> ToolResult:
        msg = f"session search unavailable: {reason}"
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.warning(
            "session_search.execute: store unavailable — structured degradation",
            extra={"_fields": {"reason": reason, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)

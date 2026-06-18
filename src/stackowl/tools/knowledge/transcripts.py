"""transcripts — return the ORDERED full message log of a past session.

Read-only, ordered retrieval of one session's turns from the canonical
conversation store (``messages`` + ``conversations``, migration 0002). This is
the FULL-RETRIEVAL counterpart to ``session_search``: where ``session_search``
ranks/filters/windows turns, ``transcripts`` returns the whole conversation in
order (pagination-bounded) so the caller can read it end to end.

Shared substrate (with ``session_search`` via :mod:`session_access`):

* REDACTION — every returned turn is passed through :func:`redact_secrets`, so
  secrets a user pasted into a turn (or that appear in an included tool payload)
  are masked before re-entering context.
* VISIBILITY GUARD — :func:`resolve_visibility` limits cross-session access:
  the caller's current session by default; another session only if same owner
  (``owl_name``). Cross-owner reads are refused.

Tool-call payloads (impl vote): user/assistant turns are returned by default and
``tool`` turns are EXCLUDED (they are bulky and the most likely place for a
leaked secret/raw blob). Pass ``include_tool_calls=True`` to include them — and
when included, their content is ALSO run through :func:`redact_secrets`.

Live-meeting capture is explicitly NOT implemented here (Phase 2 backlog): this
tool reads ALREADY-PERSISTED conversation turns; capturing a live audio/meeting
stream into the transcript store is a separate, deferred concern.

Severity ``read``; ``toolset_group="knowledge"``.

Provenance / port-vs-build: BUILD — ordered full-session retrieval over our own
SQLite store; shares the ported redaction/visibility substrate with
``session_search``. See ``_bmad-output/research/tool-port-analysis.md``.
"""

from __future__ import annotations

import time

from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.knowledge.session_access import redact_secrets, resolve_visibility

_DEFAULT_LIMIT = 200
_MAX_LIMIT = 1000

# Ordered full transcript for a session, pagination-bounded.
_TRANSCRIPT_SQL = """
SELECT m.id, m.role, m.content, m.created_at
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
WHERE c.session_id = ?
ORDER BY m.created_at ASC, m.id ASC
LIMIT ? OFFSET ?
"""

_TOOL_ROLE = "tool"


class TranscriptsTool(Tool):
    """Return the ordered, redacted, owner-scoped transcript of a session."""

    @property
    def name(self) -> str:
        return "transcripts"

    @property
    def description(self) -> str:
        return (
            "Return the ORDERED full transcript (message log) of a past session "
            "by session_id. Distinct from session_search: this is the complete "
            "in-order conversation, not a ranked search. user/assistant turns by "
            "default; pass include_tool_calls=true to also include (redacted) "
            "tool turns. Reads the current session by default; another session "
            "must share the owner (cross-owner reads are refused). Secrets are "
            "masked. "
            "LANE: reading a whole prior conversation in order. "
            "ANTI-LANE: to FIND a specific past utterance use session_search; for "
            "durable facts use memory; for a procedure use skill_view."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": (
                        "Session whose transcript to return. Defaults to the "
                        "current session; another session must share the owner."
                    ),
                },
                "include_tool_calls": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include (redacted) tool turns. Default off.",
                },
                "limit": {
                    "type": "integer",
                    "default": _DEFAULT_LIMIT,
                    "description": f"Max turns (1-{_MAX_LIMIT}).",
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "description": "Pagination offset.",
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

    # ------------------------------------------------------------------ execute

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.tool.info("transcripts.execute: entry", extra={"_fields": {}})

        db = get_services().db_pool
        if db is None:
            return self._unavailable("no database pool is configured", t0)

        try:
            # 2. DECISION — resolve which session we may read.
            sid = kwargs.get("session_id")
            sid_str = str(sid).strip() if isinstance(sid, str) else None
            vis = await resolve_visibility(db, sid_str)
            if not vis.allowed:
                return self._err(vis.reason, t0)

            include_tools = bool(kwargs.get("include_tool_calls", False))
            limit = self._coerce(kwargs.get("limit"), _DEFAULT_LIMIT, _MAX_LIMIT)
            offset = self._coerce(kwargs.get("offset"), 0, 10**9, lo=0)

            rows = await db.fetch_all(_TRANSCRIPT_SQL, (vis.session_id, limit, offset))
            if not include_tools:
                rows = [r for r in rows if str(r.get("role")) != _TOOL_ROLE]
            # 3. STEP
            log.tool.debug(
                "transcripts.execute: fetched",
                extra={"_fields": {"rows": len(rows), "include_tools": include_tools}},
            )
            return self._ok(self._render(rows, vis.session_id), t0, len(rows))
        except Exception as exc:  # self-healing — degrade, never raise.
            log.tool.error(
                "transcripts.execute: failed — degrading to structured error",
                exc_info=exc,
            )
            return self._unavailable(f"{type(exc).__name__}: {exc}", t0)

    # ------------------------------------------------------------------ helpers

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
    def _render(rows: list[dict[str, object]], session_id: str | None) -> str:
        """Render the ordered transcript with REDACTION on every turn."""
        header = f"transcript of session {session_id}"
        if not rows:
            return f"{header}\n(no turns — empty or unknown session)"
        lines = [header]
        for r in rows:
            role = str(r.get("role", "?"))
            content = redact_secrets(str(r.get("content", "")))
            lines.append(f"  {role}: {content}")
        return "\n".join(lines)

    def _ok(self, output: str, t0: float, count: int) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "transcripts.execute: exit",
            extra={"_fields": {"success": True, "turns": count, "duration_ms": duration_ms}},
        )
        return ToolResult(success=True, output=output, duration_ms=duration_ms)

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "transcripts.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)

    @staticmethod
    def _unavailable(reason: str, t0: float) -> ToolResult:
        msg = f"transcripts unavailable: {reason}"
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.warning(
            "transcripts.execute: store unavailable — structured degradation",
            extra={"_fields": {"reason": reason, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)

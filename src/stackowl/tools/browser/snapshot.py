"""browser_snapshot — accessibility snapshot with stable, click-resolvable refs.

Returns the current page's accessibility tree as compact, LLM-legible text where
every interactive node carries a stable ``[ref=eN]`` marker. The model then
clicks a node by passing that ref to ``browser_click(ref=...)`` — far more robust
than guessing a CSS selector from a screenshot (the failure mode shifts from a
*syntax* error to a *reasoning* error, which models handle better).

Refs come from the in-process browser engine's native AI-accessibility snapshot
(``locator.aria_snapshot(mode="ai")``) and resolve through the engine's
``aria-ref`` selector engine — cross-browser, in-process, with no
DevTools-protocol dependency (the backend this build runs does not expose one).

A summarize-threshold + interactive-ref-preserving truncation keeps large pages
inside the context budget of small-context models: static text is dropped first,
and a node carrying a ref is NEVER dropped silently (so the model can always
target what it sees).

Provenance / port-vs-build: see
``_bmad-output/research/tool-port-analysis.md`` (E2 ``browser_snapshot`` row,
HYBRID — summarize-threshold + ref-naming) and
``_bmad-output/planning-artifacts/stories/E2-browser/E2-LOCKED-DECISIONS.md``
(keystone revised 2026-05-29: engine-native refs replace the infeasible
DevTools-protocol scheme on this browser backend).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.browser.tools import _err, _ok, _services_or_unavailable

# Truncation policy (ported threshold concept). The depth cap is applied by the
# engine itself via aria_snapshot(depth=...); the char threshold is the final
# guard so a deep-but-wide page still cannot blow the context budget.
_DEFAULT_SNAPSHOT_DEPTH = 25
_SNAPSHOT_SUMMARIZE_THRESHOLD = 12_000  # chars (operator vote #1, confirmed at impl)
_DEFAULT_MAX_REFS = 500  # hard cap so an all-interactive page stays bounded
_MARKER_RESERVE = 96  # chars held back from the static budget for the trailing marker
_REF_MARKER = "[ref="  # engine-emitted, opaque interactive-node marker


def _as_int(value: object, default: int) -> int:
    """Coerce a tool arg to int, falling back to ``default`` on bad input.

    Self-healing: a malformed ``depth="abc"`` must NOT raise out of execute().
    """
    if isinstance(value, bool):  # bool is an int subclass — reject as malformed
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class SnapshotTruncation:
    """Result of :func:`truncate_snapshot` — the text plus what (if anything) was cut."""

    text: str
    truncated: bool
    dropped_static_lines: int
    refs_kept: int
    refs_omitted: int


def truncate_snapshot(
    text: str, *, threshold: int, max_refs: int = _DEFAULT_MAX_REFS,
) -> SnapshotTruncation:
    """Bound a snapshot to a char budget, biased toward interactive elements.

    Lines carrying a ``[ref=...]`` marker are kept ahead of static text (the
    model must never lose a ref it could target). Static (ref-less) lines are
    kept in order only while the running length stays under ``threshold`` (minus
    a small reserve for the marker). To keep even a pathological all-interactive
    page bounded, at most ``max_refs`` ref lines are kept; any beyond that are
    dropped too. Whatever is dropped is announced in a trailing marker line — the
    omission is never silent. ``refs_kept`` is the authoritative ref count (no
    re-scan needed by callers).
    """
    refs_total = text.count(_REF_MARKER)
    if len(text) <= threshold:
        return SnapshotTruncation(text, False, 0, refs_total, 0)
    budget = max(threshold - _MARKER_RESERVE, 0)
    kept: list[str] = []
    running = 0
    dropped_static = 0
    refs_kept = 0
    refs_omitted = 0
    for line in text.splitlines():
        if _REF_MARKER in line:
            if refs_kept < max_refs:
                kept.append(line)
                running += len(line) + 1
                refs_kept += 1
            else:
                refs_omitted += 1
        elif running + len(line) + 1 <= budget:
            kept.append(line)
            running += len(line) + 1
        else:
            dropped_static += 1
    notes: list[str] = []
    if dropped_static:
        notes.append(f"{dropped_static} static lines")
    if refs_omitted:
        notes.append(f"{refs_omitted} interactive refs")
    if notes:
        kept.append(f"… ({', '.join(notes)} omitted; narrow with depth= or a selector)")
    return SnapshotTruncation("\n".join(kept), bool(notes), dropped_static, refs_kept, refs_omitted)


class BrowserSnapshotTool(Tool):
    """Capture the page accessibility tree with stable, clickable refs."""

    @property
    def name(self) -> str:
        return "browser_snapshot"

    @property
    def description(self) -> str:
        return (
            "Capture the current page's accessibility tree with stable [ref=eN] markers on "
            "interactive elements (each shows its role + accessible name). Click an element "
            "later with browser_click(ref=...). Large pages are truncated, but every "
            "interactive ref is preserved. Returns {snapshot, ref_count, truncated}."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "page_handle": {"type": "string"},
                "depth": {
                    "type": "integer",
                    "description": "Max tree depth to capture (truncation depth cap).",
                    "default": _DEFAULT_SNAPSHOT_DEPTH,
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Char budget; static text beyond it is dropped (refs kept).",
                    "default": _SNAPSHOT_SUMMARIZE_THRESHOLD,
                },
            },
            "required": ["session_id"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            toolset_group="browser",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        session_id = str(kwargs.get("session_id", ""))
        page_handle = kwargs.get("page_handle")
        depth = _as_int(kwargs.get("depth", _DEFAULT_SNAPSHOT_DEPTH), _DEFAULT_SNAPSHOT_DEPTH)
        max_chars = _as_int(kwargs.get("max_chars", _SNAPSHOT_SUMMARIZE_THRESHOLD), _SNAPSHOT_SUMMARIZE_THRESHOLD)
        log.tool.info(
            "browser_snapshot.execute: entry",
            extra={"_fields": {"session_id": session_id, "depth": depth, "max_chars": max_chars}},
        )
        # Self-healing: no live browser substrate → structured result, never raise.
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0, tool="browser_snapshot")
        try:
            sess, page, ph = await sessions.get_page(
                session_id, str(page_handle) if page_handle else None
            )
        except Exception as exc:
            return _err(f"browser session unavailable: {type(exc).__name__}: {exc}", t0, tool="browser_snapshot")

        # 2. DECISION — engine-native AI accessibility snapshot (stable [ref=] markers).
        log.tool.debug("browser_snapshot.execute: using aria-ai snapshot", extra={"_fields": {"depth": depth}})
        try:
            raw = await self._aria_snapshot(page, depth=depth)
        except Exception as exc:
            return _err(f"snapshot failed: {type(exc).__name__}: {exc}", t0, tool="browser_snapshot")

        # 3. STEP — truncate over the char budget, preserving interactive refs.
        trunc = truncate_snapshot(str(raw or ""), threshold=max_chars)
        log.tool.debug(
            "browser_snapshot.execute: snapshot built",
            extra={"_fields": {
                "raw_len": len(str(raw or "")),
                "ref_count": trunc.refs_kept,
                "truncated": trunc.truncated,
            }},
        )
        # Surface any pending JS dialogs so the model can act on them via
        # browser_dialog (it discovers dialog_id here). E2-S6.
        obs = getattr(sess, "observers", {}).get(ph)
        pending_dialogs = (
            [
                {"dialog_id": d.dialog_id, "type": d.type, "message": d.message}
                for d in obs.dialogs.values()
            ]
            if obs is not None else []
        )
        # 4. EXIT
        return _ok(
            {
                "snapshot": trunc.text,
                "ref_count": trunc.refs_kept,
                "truncated": trunc.truncated,
                "dropped_static_lines": trunc.dropped_static_lines,
                "refs_omitted": trunc.refs_omitted,
                "pending_dialogs": pending_dialogs,
                "page_handle": ph,
            },
            t0,
            tool="browser_snapshot",
        )

    @staticmethod
    async def _aria_snapshot(page: Any, *, depth: int) -> str:
        """Engine-native AI accessibility snapshot, degrading across engine versions.

        Prefers ``mode="ai"`` (emits [ref=] markers) + ``depth`` cap; falls back to
        the bare signature on older engines so the tool still returns a usable tree.
        """
        locator = page.locator("body")
        try:
            return str(await locator.aria_snapshot(mode="ai", depth=depth))
        except TypeError as exc:
            # Older engine signature without mode/depth kwargs — degrade, but log
            # so the degradation is observable (never a silent catch).
            log.tool.debug(
                "browser_snapshot._aria_snapshot: mode/depth unsupported — degrading to bare signature",
                extra={"_fields": {"exc": str(exc)}},
            )
            return str(await locator.aria_snapshot())

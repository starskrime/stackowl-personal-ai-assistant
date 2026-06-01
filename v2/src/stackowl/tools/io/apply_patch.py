"""ApplyPatchTool — atomically apply a V4A unified diff across one or more files.

Parses a V4A patch (Update / Add / Delete / Move hunks via
:mod:`stackowl.tools.io.patch_parser`), then applies it under three layered
safety guarantees:

1. **Path-guard every target first.** EVERY path parsed out of the patch body —
   Update, Add, Delete, and both ends of a Move — is confined to the workspace
   with :func:`is_within_root` BEFORE any lock is acquired or any byte written.
   A single escaping target aborts the whole patch with a structured error and
   nothing is touched. (Party Security § "apply_patch is the highest-risk tool —
   path-guard EVERY target incl Add/Delete/move, BEFORE acquiring the write lock".)
2. **Sorted multi-file lock.** All target paths are locked in a stable sorted
   order, so two concurrent patches that share files can never deadlock.
3. **Atomic apply with full rollback.** Pre-images of every modified/deleted/
   moved file are snapshotted through the SHARED :class:`UndoStore` (so a
   successful-but-wrong patch is one ``undo_write`` away), and created files are
   tracked. If ANY hunk fails, ALL files are restored to their pre-images and
   all freshly-created files are removed — partial application is impossible by
   construction.

The tool never raises: any parse/apply failure self-heals into a full rollback
and a structured :class:`ToolResult`.

Provenance / port-vs-build: the V4A hunk-application algorithm (fuzzy-anchored
search/replace, context-hint window fallback, addition-only insertion) is a PORT
— see ``_bmad-output/research/tool-port-analysis.md`` (E3 ``apply_patch`` row).
The path-guard-every-target ordering, sorted multi-file lock, and snapshot-based
atomic rollback are StackOwl-native (BUILD), translated to clean async Python
rather than ported, because the upstream apply path is coupled to a foreign
file-ops interface and has no rollback.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.io.fuzzy_match import fuzzy_find_and_replace
from stackowl.tools.io.patch_parser import (
    Hunk,
    OperationType,
    PatchOperation,
    count_occurrences,
    parse_v4a_patch,
)
from stackowl.tools.io.path_guard import is_within_root as _guard
from stackowl.tools.io.path_guard import resolve_in_workspace as _resolve
from stackowl.tools.io.undo_store import UndoStore

# Reject patches larger than this (defends against pathological/oversized input
# and accidental whole-repo dumps). 2 MiB is far above any sane multi-file edit.
_MAX_PATCH_BYTES = 2 * 1024 * 1024

# Per-process lock table so two ApplyPatchTool calls touching overlapping files
# serialise on the SAME asyncio.Lock objects (one Lock keyed by resolved path).
_PATH_LOCKS: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


@dataclass
class _ApplyState:
    """Mutable bookkeeping for a single patch application (for rollback)."""

    # In-memory pre-images (path, original content) for Update/Delete/Move-source.
    # Rollback restores from THIS buffer — NOT the bounded UndoStore ring — so a
    # patch touching >ring-size files still rolls back fully (atomicity, C1).
    pre_images: list[tuple[Path, str]] = field(default_factory=list)
    # absolute paths of files we CREATED (Add / Move-dest) — removed on rollback.
    created: list[Path] = field(default_factory=list)
    # per-file human summary lines for the success report.
    summary: list[str] = field(default_factory=list)
    diffs: list[str] = field(default_factory=list)


class ApplyPatchTool(Tool):
    """Apply a V4A multi-file unified diff atomically (full rollback on any failure)."""

    def __init__(self, store: UndoStore | None = None) -> None:
        self._store = store or UndoStore()

    @property
    def name(self) -> str:
        return "apply_patch"

    @property
    def description(self) -> str:
        return (
            "Apply a V4A unified-diff patch across one or more files atomically. "
            "Use this for multi-file, coordinated changes or to create/delete/move "
            "files; use 'edit' for a single localized change in one file. Supports "
            "*** Update/Add/Delete/Move File hunks with context disambiguation and "
            "fuzzy anchoring. Every target is confined to the workspace. If any hunk "
            "fails, ALL files roll back to their pre-images (no partial application). "
            "Returns a per-file summary and an undo token."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "patch": {
                    "type": "string",
                    "description": (
                        "The V4A patch text. Wrap operations in '*** Begin Patch' / "
                        "'*** End Patch' with '*** Update File:', '*** Add File:', "
                        "'*** Delete File:', or '*** Move File: a -> b' markers."
                    ),
                },
            },
            "required": ["patch"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
            toolset_group="code",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        patch = str(kwargs.get("patch", ""))
        log.tool.debug("apply_patch.execute: entry", extra={"_fields": {"patch_len": len(patch)}})

        if not patch:
            return self._err("Missing patch", t0)
        # Max patch size guard — reject oversized input before any parsing/IO.
        patch_bytes = len(patch.encode("utf-8"))
        if patch_bytes > _MAX_PATCH_BYTES:
            log.tool.warning(
                "apply_patch.execute: patch too large",
                extra={"_fields": {"bytes": patch_bytes, "max": _MAX_PATCH_BYTES}},
            )
            return self._err(
                f"Patch too large ({patch_bytes} bytes; limit {_MAX_PATCH_BYTES}). "
                "Split it into smaller patches.",
                t0,
            )

        # Parse (pure — no IO yet).
        operations, parse_err = parse_v4a_patch(patch)
        if parse_err:
            log.tool.info("apply_patch.execute: parse error", extra={"_fields": {"err": parse_err}})
            return self._err(f"Patch parse failed (nothing applied): {parse_err}", t0)
        if not operations:
            return self._err("Patch contained no operations.", t0)

        # 2. DECISION — collect EVERY target path and path-guard it BEFORE any lock
        # or write. This is the #1 security condition: an Add/Delete/Move target
        # that escapes the workspace must abort the whole patch with nothing touched.
        targets = self._collect_targets(operations)
        for tgt in targets:
            if not _guard(tgt):
                log.tool.warning(
                    "apply_patch.execute: target escapes workspace — refusing entire patch",
                    extra={"_fields": {"target": str(tgt)}},
                )
                return self._err(
                    f"Path traversal denied: '{tgt}' resolves outside the workspace. "
                    "No files were modified.",
                    t0,
                )

        # Resolve + sort the lock set (deadlock-free ordering). Lock both Move ends.
        lock_paths = sorted({str(p.resolve()) for p in targets})
        log.tool.debug(
            "apply_patch.execute: guarded + locking",
            extra={"_fields": {"ops": len(operations), "locks": len(lock_paths)}},
        )

        # 3. STEP — acquire the sorted lock set, snapshot, apply, rollback-on-failure.
        async with contextlib.AsyncExitStack() as stack:
            for lp in lock_paths:
                await stack.enter_async_context(_PATH_LOCKS[lp])
            return await self._apply_all(operations, t0)

        # Unreachable (AsyncExitStack body always returns), but satisfies typing.
        return self._err("Internal error: apply context exited without a result.", t0)

    # ------------------------------------------------------------------ apply core

    async def _apply_all(self, operations: list[PatchOperation], t0: float) -> ToolResult:
        """Apply every operation; on the first failure, roll everything back."""
        state = _ApplyState()
        try:
            for op in operations:
                if op.operation == OperationType.UPDATE:
                    self._apply_update(op, state)
                elif op.operation == OperationType.ADD:
                    self._apply_add(op, state)
                elif op.operation == OperationType.DELETE:
                    self._apply_delete(op, state)
                elif op.operation == OperationType.MOVE:
                    self._apply_move(op, state)
        except _ApplyError as exc:
            self._rollback(state)
            log.tool.info(
                "apply_patch.execute: hunk failed — rolled back all files",
                extra={"_fields": {"reason": str(exc)}},
            )
            return self._err(
                f"Patch apply failed and ALL files were rolled back (no partial "
                f"application): {exc}",
                t0,
            )
        except OSError as exc:
            self._rollback(state)
            log.tool.error(
                "apply_patch.execute: filesystem error — rolled back all files",
                exc_info=exc,
            )
            return self._err(
                f"Filesystem error during apply; ALL files were rolled back: {exc}",
                t0,
            )

        # 4. EXIT — success. Record ONE group snapshot covering every modified file
        # (pre-images) and every created file (to delete on undo), so the WHOLE
        # patch is reverted by a single undo_write — and it occupies one ring slot,
        # immune to per-file eviction (M1).
        duration_ms = (time.monotonic() - t0) * 1000
        group_token = self._store.snapshot_group(state.pre_images, state.created)
        undo_hint = f"Undo token: {group_token} (call undo_write to revert this ENTIRE patch — all files)"
        log.tool.debug(
            "apply_patch.execute: exit",
            extra={
                "_fields": {
                    "files": len(state.summary),
                    "created": len(state.created),
                    "pre_images": len(state.pre_images),
                    "group_token": group_token,
                    "duration_ms": duration_ms,
                }
            },
        )
        body = "\n".join(state.summary)
        diff = "\n".join(d for d in state.diffs if d)
        payload = f"Patch applied to {len(state.summary)} file(s).\n{undo_hint}\n\n{body}"
        if diff:
            payload += f"\n\n{diff}"
        return ToolResult(success=True, output=payload, duration_ms=duration_ms)

    def _rollback(self, state: _ApplyState) -> None:
        """Restore every modified file from its IN-MEMORY pre-image and remove
        every created file.

        Restores from the in-process ``pre_images`` buffer (not the bounded
        UndoStore ring), so a patch touching more files than the ring holds still
        rolls back fully — atomicity is independent of the eviction policy (C1).
        Best-effort and never raises: each write/unlink is independent.
        """
        for path, content in state.pre_images:
            try:
                path.write_text(content, encoding="utf-8", newline="")
            except OSError as exc:
                log.tool.warning(
                    "apply_patch._rollback: pre-image restore failed",
                    extra={"_fields": {"path": str(path), "err": str(exc)}},
                )
        for created in state.created:
            try:
                created.unlink(missing_ok=True)
            except OSError as exc:
                log.tool.warning(
                    "apply_patch._rollback: could not remove created file",
                    extra={"_fields": {"path": str(created), "err": str(exc)}},
                )

    # ------------------------------------------------------------------ per-op apply

    def _apply_update(self, op: PatchOperation, state: _ApplyState) -> None:
        target = _resolve(op.file_path)
        try:
            raw = target.read_text(encoding="utf-8", newline="")
        except FileNotFoundError as exc:
            raise _ApplyError(f"UPDATE {op.file_path}: file not found") from exc

        newline = self._detect_newline(raw)
        current = raw.replace("\r\n", "\n").replace("\r", "\n")
        new_content = self._apply_hunks(op, current)

        # Snapshot pre-image BEFORE writing so rollback (and undo_write) can revert.
        state.pre_images.append((target, raw))
        out = new_content.replace("\n", newline) if newline != "\n" else new_content
        target.write_text(out, encoding="utf-8", newline="")

        state.summary.append(f"updated  {op.file_path}")
        state.diffs.append(self._unified_diff(current, new_content, op.file_path))

    def _apply_add(self, op: PatchOperation, state: _ApplyState) -> None:
        target = _resolve(op.file_path)
        # Add over an existing file is an error (would clobber).
        if target.exists():
            raise _ApplyError(f"ADD {op.file_path}: file already exists (would overwrite)")

        content_lines: list[str] = []
        for hunk in op.hunks:
            for line in hunk.lines:
                if line.prefix == "+":
                    content_lines.append(line.content)
        content = "\n".join(content_lines)

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        state.created.append(target)
        state.summary.append(f"created  {op.file_path}")
        diff = f"--- /dev/null\n+++ b/{op.file_path}\n" + "\n".join(
            f"+{line}" for line in content_lines
        )
        state.diffs.append(diff)

    def _apply_delete(self, op: PatchOperation, state: _ApplyState) -> None:
        target = _resolve(op.file_path)
        try:
            raw = target.read_text(encoding="utf-8", newline="")
        except FileNotFoundError as exc:
            raise _ApplyError(f"DELETE {op.file_path}: file not found") from exc

        # Snapshot the pre-image so the delete is reversible (rollback + undo_write).
        state.pre_images.append((target, raw))
        target.unlink()
        state.summary.append(f"deleted  {op.file_path}")
        removed = raw.splitlines(keepends=True)
        state.diffs.append(
            "".join(
                difflib.unified_diff(
                    removed, [], fromfile=f"a/{op.file_path}", tofile="/dev/null"
                )
            )
            or f"# Deleted: {op.file_path}"
        )

    def _apply_move(self, op: PatchOperation, state: _ApplyState) -> None:
        src = _resolve(op.file_path)
        assert op.new_path is not None  # parser guarantees this for MOVE
        dst = _resolve(op.new_path)
        try:
            raw = src.read_text(encoding="utf-8", newline="")
        except FileNotFoundError as exc:
            raise _ApplyError(f"MOVE {op.file_path}: source not found") from exc
        if dst.exists():
            raise _ApplyError(f"MOVE -> {op.new_path}: destination already exists (would overwrite)")

        # Snapshot the source pre-image (so rollback restores it), create the
        # destination (tracked for removal on rollback), then drop the source.
        state.pre_images.append((src, raw))
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(raw, encoding="utf-8", newline="")
        state.created.append(dst)
        src.unlink()
        state.summary.append(f"moved    {op.file_path} -> {op.new_path}")
        state.diffs.append(f"# Moved: {op.file_path} -> {op.new_path}")

    # ------------------------------------------------------------------ hunk engine

    def _apply_hunks(self, op: PatchOperation, current: str) -> str:
        """Apply every UPDATE hunk to *current*; raise :class:`_ApplyError` on a miss.

        Ported V4A apply algorithm: build a search pattern from context (' ') and
        removed ('-') lines and a replacement from context and added ('+') lines,
        fuzzy-find-and-replace it; on a miss retry inside a window around the
        context hint; addition-only hunks are inserted after the hint (or appended).
        """
        new_content = current
        for hunk in op.hunks:
            search_lines: list[str] = []
            replace_lines: list[str] = []
            for line in hunk.lines:
                if line.prefix == " ":
                    search_lines.append(line.content)
                    replace_lines.append(line.content)
                elif line.prefix == "-":
                    search_lines.append(line.content)
                elif line.prefix == "+":
                    replace_lines.append(line.content)

            if search_lines:
                new_content = self._apply_change_hunk(
                    op, hunk, new_content, search_lines, replace_lines
                )
            else:
                new_content = self._apply_addition_hunk(op, hunk, new_content, replace_lines)
        return new_content

    def _apply_change_hunk(
        self,
        op: PatchOperation,
        hunk: Hunk,
        new_content: str,
        search_lines: list[str],
        replace_lines: list[str],
    ) -> str:
        search_pattern = "\n".join(search_lines)
        replacement = "\n".join(replace_lines)

        replaced, count, _strategy, error = fuzzy_find_and_replace(
            new_content, search_pattern, replacement, replace_all=False
        )
        if error and count == 0:
            # Retry inside a window around the context hint, if any.
            if hunk.context_hint:
                hint_pos = new_content.find(hunk.context_hint)
                if hint_pos != -1:
                    window_start = max(0, hint_pos - 500)
                    window_end = min(len(new_content), hint_pos + 2000)
                    window = new_content[window_start:window_end]
                    window_new, w_count, _w_strategy, _w_error = fuzzy_find_and_replace(
                        window, search_pattern, replacement, replace_all=False
                    )
                    if w_count > 0:
                        return new_content[:window_start] + window_new + new_content[window_end:]
            label = f"'{hunk.context_hint}'" if hunk.context_hint else "(no hint)"
            raise _ApplyError(f"{op.file_path}: hunk {label} not found — {error}")
        return replaced

    def _apply_addition_hunk(
        self,
        op: PatchOperation,
        hunk: Hunk,
        new_content: str,
        replace_lines: list[str],
    ) -> str:
        insert_text = "\n".join(replace_lines)
        if not hunk.context_hint:
            return new_content.rstrip("\n") + "\n" + insert_text + "\n"

        occurrences = count_occurrences(new_content, hunk.context_hint)
        if occurrences == 0:
            # Hint not found — append at end as a safe fallback.
            return new_content.rstrip("\n") + "\n" + insert_text + "\n"
        if occurrences > 1:
            raise _ApplyError(
                f"{op.file_path}: addition-only hunk context hint "
                f"'{hunk.context_hint}' is ambiguous ({occurrences} occurrences) — "
                "provide a more unique hint"
            )
        hint_pos = new_content.find(hunk.context_hint)
        eol = new_content.find("\n", hint_pos)
        if eol != -1:
            return new_content[: eol + 1] + insert_text + "\n" + new_content[eol + 1 :]
        return new_content + "\n" + insert_text

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _collect_targets(operations: list[PatchOperation]) -> list[Path]:
        """Every filesystem path the patch will touch — both ends of a Move."""
        targets: list[Path] = []
        for op in operations:
            targets.append(_resolve(op.file_path))
            if op.operation == OperationType.MOVE and op.new_path:
                targets.append(_resolve(op.new_path))
        return targets

    @staticmethod
    def _detect_newline(raw: str) -> str:
        """Return the dominant line ending of *raw*: '\\r\\n', '\\r', or '\\n'."""
        if raw.count("\r\n") > 0:
            return "\r\n"
        if "\r" in raw and "\n" not in raw:
            return "\r"
        return "\n"

    @staticmethod
    def _unified_diff(before: str, after: str, path: str) -> str:
        return "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)


class _ApplyError(Exception):
    """Raised when a hunk/operation cannot be applied — triggers full rollback."""

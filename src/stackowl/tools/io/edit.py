"""EditTool — fuzzy-assisted unique string replacement in a workspace file.

Locates ``old_string`` via the multi-strategy fuzzy matcher (whitespace /
indentation / unicode drift tolerant), requires a UNIQUE match, preserves the
file's CRLF/LF line endings, snapshots the pre-image (so the edit is one
``undo_write`` away), writes, then read-back verifies the change landed. On a
near-miss it returns an escalating structured hint (nearest span + similarity +
a minimal char-diff). The target is confined to the workspace and a wrong
locate/ambiguous match never partially writes the file.

Provenance / port-vs-build: PORT of the fuzzy-locate + CRLF-preservation +
post-write-verify + escalating-hint algorithm; see
``_bmad-output/research/tool-port-analysis.md`` (E3 ``edit`` row).
port-source: upstream-agent@38441a7d7.
"""

from __future__ import annotations

import difflib
import time
from dataclasses import dataclass
from difflib import SequenceMatcher

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.io.fuzzy_match import fuzzy_find_and_replace
from stackowl.tools.io.path_guard import data_root
from stackowl.tools.io.path_guard import is_within_root as _guard
from stackowl.tools.io.path_guard import resolve_in_workspace as _resolve
from stackowl.tools.io.undo_store import UndoStore
from stackowl.tools.system.git_tool import diff_summary, is_git_repo

# Similarity floor below which we don't bother quoting a "nearest" candidate —
# anything lower is noise that would mislead more than help.
_NEAREST_MIN_SIMILARITY = 0.30

# Fuzzy strategies that can accept a similar-but-not-exact span (esp. on short
# patterns) — a success via one of these is surfaced as a caution so a wrong-but-
# similar-line edit is caught (the unified diff shows what actually changed).
_LOW_CONFIDENCE_STRATEGIES = frozenset({"indentation_flexible", "block_anchor", "context_aware"})


@dataclass
class _FailureTracker:
    """Counts consecutive identical edit failures so the hint can escalate.

    Process-level (tools can't read PipelineState until E5); keyed by
    (path, old_string) so unrelated edits don't inflate each other's count.
    """

    last_key: tuple[str, str] | None = None
    count: int = 0

    def observe(self, key: tuple[str, str]) -> int:
        if key == self.last_key:
            self.count += 1
        else:
            self.last_key = key
            self.count = 1
        return self.count


class EditTool(Tool):
    """Replace a unique ``old_string`` with ``new_string`` in a workspace file."""

    def __init__(self, store: UndoStore | None = None) -> None:
        self._store = store or UndoStore()
        self._failures = _FailureTracker()

    @property
    def name(self) -> str:
        return "edit"

    @property
    def description(self) -> str:
        return (
            "Replace a unique occurrence of old_string with new_string in a file. "
            "Fuzzy-matches near-misses (whitespace/indentation drift), preserves the "
            "file's line endings, and verifies the write. old_string MUST match exactly "
            "one location — add surrounding context if it is ambiguous. Returns a unified "
            "diff and an undo token. Confined to the workspace."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative or absolute file path inside the workspace."},
                "old_string": {
                    "type": "string",
                    "description": "Exact text to find (must match exactly one location).",
                },
                "new_string": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old_string", "new_string"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
            commit_coupling="transactional",
            toolset_group="code",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        path_str = str(kwargs.get("path", ""))
        old_string = str(kwargs.get("old_string", ""))
        new_string = str(kwargs.get("new_string", ""))
        log.tool.debug(
            "edit.execute: entry",
            extra={"_fields": {"path": path_str, "old_len": len(old_string), "new_len": len(new_string)}},
        )

        if not path_str:
            return self._err("Missing path", t0, committed=False)
        if not old_string:
            return self._err("old_string cannot be empty", t0, committed=False)

        # A relative path anchors UNDER the workspace (mirrors search_files hit
        # paths), so a relative hit piped straight in round-trips. Guard confines.
        target = _resolve(path_str)
        # Path guard FIRST — never touch a file outside the workspace.
        if not _guard(target):
            log.tool.warning("edit.execute: path traversal denied", extra={"_fields": {"path": path_str}})
            return self._err("Path traversal denied", t0, committed=False)

        # Read the file (self-healing on missing/unreadable). newline="" disables
        # universal-newline translation so the on-disk CRLF/LF survives detection.
        try:
            raw = target.read_text(encoding="utf-8", newline="")
        except FileNotFoundError:
            return self._err(f"File not found: {path_str}", t0, committed=False)
        except OSError as exc:
            log.tool.error("edit.execute: read failed", exc_info=exc, extra={"_fields": {"path": path_str}})
            return self._err(f"Could not read {path_str}: {exc}", t0, committed=False)

        # 2. DECISION — detect and remember the file's line-ending style, then
        # work on an LF-normalized copy so the matcher's '\n' line splits are
        # correct regardless of the on-disk convention.
        newline = self._detect_newline(raw)
        content = raw.replace("\r\n", "\n").replace("\r", "\n")
        old_norm = old_string.replace("\r\n", "\n").replace("\r", "\n")
        new_norm = new_string.replace("\r\n", "\n").replace("\r", "\n")
        log.tool.debug("edit.execute: line-ending detected", extra={"_fields": {"newline": repr(newline)}})

        # Locate + replace (require a UNIQUE match — replace_all=False).
        new_content, match_count, strategy, error = fuzzy_find_and_replace(
            content, old_norm, new_norm, replace_all=False
        )

        if error or match_count == 0:
            return self._handle_no_match(path_str, old_norm, content, error, match_count, t0)

        # Success path resets the escalation counter for this (path, old_string).
        self._failures.observe((path_str, old_norm))
        self._failures.count = 0

        # 3. STEP — snapshot pre-image, then write with ORIGINAL line endings.
        token = self._store.snapshot(target, raw)
        out_bytes = new_content.replace("\n", newline) if newline != "\n" else new_content
        try:
            target.write_text(out_bytes, encoding="utf-8", newline="")
        except OSError as exc:
            log.tool.error("edit.execute: write failed", exc_info=exc, extra={"_fields": {"path": path_str}})
            return self._err(f"Failed to write {path_str}: {exc}", t0)

        # Post-write read-back verify (line-ending-insensitive compare).
        try:
            verify_raw = target.read_text(encoding="utf-8", newline="")
        except OSError as exc:
            return self._err(f"Post-write verification failed: could not re-read {path_str}: {exc}", t0)
        verify_norm = verify_raw.replace("\r\n", "\n").replace("\r", "\n")
        if verify_norm != new_content:
            # Self-healing: the persisted write is corrupt — auto-restore the
            # pre-image so the file returns to its pre-edit state rather than being
            # left in a bad state pending a manual undo_write.
            restored, _msg = self._store.restore(token)
            log.tool.error(
                "edit.execute: post-write verify mismatch — auto-restored",
                extra={"_fields": {"path": path_str, "wrote": len(new_content),
                                   "read": len(verify_norm), "restored": restored}},
            )
            return self._err(
                f"Post-write verification failed for {path_str}: on-disk content differed from the intended "
                f"write, so the file was auto-restored to its pre-edit state "
                f"({'restore ok' if restored else 'restore FAILED — use undo_write'}). Re-read and try again.",
                t0,
            )

        # 4. EXIT — unified diff + undo token. Surface low-confidence fuzzy hits so
        # the model/user can catch a wrong-but-similar-line edit (the diff shows it).
        diff = self._unified_diff(content, new_content, path_str)
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.debug(
            "edit.execute: exit",
            extra={"_fields": {"path": path_str, "strategy": strategy, "token": token, "duration_ms": duration_ms}},
        )
        caution = ""
        if strategy in _LOW_CONFIDENCE_STRATEGIES:
            caution = (
                f"\n⚠ Matched via fuzzy strategy '{strategy}' (NOT an exact match). Confirm the diff below "
                "hit the intended line; if it edited the wrong one, call undo_write.\n"
            )
        payload = (
            f"Edited {path_str} (matched via {strategy}).\n"
            f"Undo token: {token}\n{caution}\n"
            f"{diff}"
        )
        # Independent confirmation of what changed, alongside (never replacing)
        # the self-computed diff above — best-effort: any failure is logged
        # and omitted, never fails this already-successful edit (research
        # artifact §3 proposal 4).
        repo_dir = str(data_root())
        if await is_git_repo(repo_dir):
            git_diff = await diff_summary(repo_dir)
            if git_diff.success:
                payload += f"\n\n--- git diff (independent check) ---\n{git_diff.output}"
            else:
                log.tool.debug(
                    "edit.execute: diff_summary failed — omitting git diff",
                    extra={"_fields": {"error": git_diff.error}},
                )
        return ToolResult(success=True, output=payload, duration_ms=duration_ms)

    # ------------------------------------------------------------------ helpers

    def _handle_no_match(
        self,
        path_str: str,
        old_norm: str,
        content: str,
        error: str | None,
        match_count: int,
        t0: float,
    ) -> ToolResult:
        """Build a structured no-match / ambiguous error; escalate on repeats.

        File is left untouched (no snapshot, no write). Ambiguous (>1 match)
        errors are reported verbatim; genuine no-match errors get a nearest-span
        + similarity + char-diff hint that escalates on consecutive failures.
        """
        base = error or f"Could not find a match for old_string in {path_str}"
        # Ambiguous match (the matcher returns count 0 but an explanatory error):
        # report verbatim, no "did you mean" noise.
        if base.startswith("Found ") and "matches" in base:
            log.tool.info("edit.execute: ambiguous match", extra={"_fields": {"path": path_str}})
            return self._err(base, t0, committed=False)

        # Escape-drift / identical-strings: surface verbatim, no nearest hint.
        if not base.startswith("Could not find"):
            return self._err(base, t0, committed=False)

        count = self._failures.observe((path_str, old_norm))
        nearest = self._nearest_candidate(old_norm, content)
        msg = base
        if nearest is not None:
            span_text, similarity, line_no = nearest
            msg += (
                f"\n\nNearest candidate (line {line_no}, similarity {similarity:.0%}):\n"
                f"{span_text}\n\n"
                + self._char_diff_hint(old_norm, span_text)
            )
        if count >= 2:
            # Escalate: the model keeps sending the same failing old_string.
            msg += (
                f"\n\n[attempt {count}] This exact edit has now failed {count} times. "
                "Re-read the file with read_file to copy the target text VERBATIM "
                "(including exact whitespace/indentation) before retrying."
            )
        log.tool.info("edit.execute: no match", extra={"_fields": {"path": path_str, "attempt": count}})
        # No match — file left untouched (no snapshot, no write): not effectful.
        return self._err(msg, t0, committed=False)

    @staticmethod
    def _detect_newline(raw: str) -> str:
        """Return the dominant line ending of *raw*: '\\r\\n', '\\r', or '\\n'."""
        crlf = raw.count("\r\n")
        if crlf > 0:
            return "\r\n"
        # Bare CR (old Mac) only if present without any LF.
        if "\r" in raw and "\n" not in raw:
            return "\r"
        return "\n"

    @staticmethod
    def _nearest_candidate(old_string: str, content: str) -> tuple[str, float, int] | None:
        """Find the content line-block most similar to *old_string*.

        Returns (span_text, similarity, 1-based_line_no) or None when nothing
        clears the similarity floor.
        """
        old_lines = old_string.splitlines() or [old_string]
        content_lines = content.splitlines()
        if not content_lines:
            return None
        window = max(1, len(old_lines))
        best: tuple[float, int] | None = None
        for i in range(len(content_lines) - window + 1):
            block = "\n".join(content_lines[i:i + window])
            sim = SequenceMatcher(None, old_string, block).ratio()
            if best is None or sim > best[0]:
                best = (sim, i)
        # Also consider a single-line anchor when old_string is one line and the
        # block window found nothing strong (handles drift in surrounding lines).
        if best is None or best[0] < _NEAREST_MIN_SIMILARITY:
            anchor = old_lines[0].strip()
            for i, line in enumerate(content_lines):
                sim = SequenceMatcher(None, anchor, line.strip()).ratio()
                if best is None or sim > best[0]:
                    best = (sim, i)
        if best is None or best[0] < _NEAREST_MIN_SIMILARITY:
            return None
        sim, idx = best
        span_text = "\n".join(content_lines[idx:idx + window])
        return span_text, sim, idx + 1

    @staticmethod
    def _char_diff_hint(old_string: str, candidate: str) -> str:
        """A minimal 'you quoted X, file has Y' hint for the first divergent run."""
        sm = SequenceMatcher(None, old_string, candidate)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "equal":
                quoted = old_string[i1:i2]
                actual = candidate[j1:j2]
                return f"You quoted {quoted!r}, file has {actual!r}."
        return "old_string and the nearest candidate are identical after normalization."

    @staticmethod
    def _unified_diff(before: str, after: str, path: str) -> str:
        diff = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
        return "".join(diff)

    @staticmethod
    def _err(msg: str, t0: float, *, committed: bool = True) -> ToolResult:
        """Structured failure. ``committed`` defaults True (conservative); callers
        pass False ONLY at a pre-execution refusal / no-match where the file was
        never touched (no snapshot, no write) so it does not trip the give-up floor.
        A post-write failure keeps the default True (the boundary may be crossed)."""
        duration_ms = (time.monotonic() - t0) * 1000
        return ToolResult(
            success=False, output="", error=msg,
            duration_ms=duration_ms, side_effect_committed=committed,
        )

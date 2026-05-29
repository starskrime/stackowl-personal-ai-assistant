"""search_files — content/filename search, ripgrep-first with a pure-Python fallback.

Ports the *algorithm* from Hermes' file search (ripgrep-first, consecutive-
identical-search loop detection, terse file:line output) — NOT its shell plumbing.
The story mandates argv-array ``asyncio.subprocess`` (never ``shell=True``), so rg
is invoked with a fixed argv and the query/path travel as separate arguments
(no command injection). When rg is absent we fall back to a pure-Python
``os.walk`` + regex scan rather than POSIX ``find`` — that fallback is
cross-platform (stock Windows has neither rg nor find) and needs no subprocess
(party Operations §). All search roots pass the shared path guard.

Provenance / port-vs-build: see ``_bmad-output/research/tool-port-analysis.md``
(E3 ``search_files`` row — PORT of the rg-first + loop-detection shape).
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.io.path_guard import data_root, is_within_root

_DEFAULT_MAX_RESULTS = 50
_RG_TIMEOUT_S = 30.0
_LOOP_THRESHOLD = 4  # consecutive identical searches → guidance, stop the loop
_MAX_LINE_LEN = 500  # trim each match line so a minified file can't flood context


def _err(msg: str, t0: float) -> ToolResult:
    duration_ms = (time.monotonic() - t0) * 1000
    log.tool.info("search_files.execute: exit", extra={"_fields": {"success": False, "error": msg}})
    return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)


def _ok(payload: str, t0: float) -> ToolResult:
    duration_ms = (time.monotonic() - t0) * 1000
    log.tool.info(
        "search_files.execute: exit",
        extra={"_fields": {"success": True, "output_len": len(payload), "duration_ms": duration_ms}},
    )
    return ToolResult(success=True, output=payload, duration_ms=duration_ms)


@dataclass
class _LoopTracker:
    """Process-level consecutive-identical-search guard.

    NOTE: not yet session-scoped (tools can't read PipelineState — lands with E5),
    so the signature includes all search args and the consequence is a *guidance*
    message, never a hard block of unrelated callers.
    """

    last_key: tuple[object, ...] | None = None
    count: int = 0

    def observe(self, key: tuple[object, ...]) -> int:
        if key == self.last_key:
            self.count += 1
        else:
            self.last_key = key
            self.count = 1
        return self.count


@dataclass
class _Match:
    path: str
    line: int
    text: str


@dataclass
class _SearchResult:
    matches: list[_Match] = field(default_factory=list)
    total: int = 0
    truncated: bool = False
    engine: str = "rg"


class SearchFilesTool(Tool):
    """Search file contents (regex) or filenames (glob), confined to the workspace."""

    def __init__(self) -> None:
        self._tracker = _LoopTracker()

    @property
    def name(self) -> str:
        return "search_files"

    @property
    def description(self) -> str:
        return (
            "Search the workspace. target='content' regex-searches inside files; "
            "target='files' finds files by glob (e.g. '*.py'). Returns terse "
            "path:line results (showing N of M). Use this instead of shelling out to "
            "grep/rg/find. Read-only; confined to the workspace."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex (content) or glob like '*.py' (files)."},
                "target": {"type": "string", "enum": ["content", "files"], "default": "content"},
                "path": {"type": "string", "description": "Sub-path to search (must stay inside workspace)."},
                "file_glob": {"type": "string", "description": "Restrict content search to files matching this glob."},
                "max_results": {"type": "integer", "default": _DEFAULT_MAX_RESULTS},
            },
            "required": ["pattern"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name, description=self.description,
            parameters=self.parameters, action_severity="read",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        pattern = str(kwargs.get("pattern", ""))
        target = str(kwargs.get("target", "content"))
        path_arg = kwargs.get("path")
        file_glob = str(kwargs["file_glob"]) if kwargs.get("file_glob") else None
        max_raw = kwargs.get("max_results", _DEFAULT_MAX_RESULTS)
        max_results = max_raw if isinstance(max_raw, int) and not isinstance(max_raw, bool) else _DEFAULT_MAX_RESULTS
        if isinstance(max_raw, str) and max_raw.isdigit():
            max_results = int(max_raw)
        max_results = max(1, min(max_results, 1000))
        log.tool.info(
            "search_files.execute: entry",
            extra={"_fields": {"target": target, "has_glob": file_glob is not None, "max_results": max_results}},
        )
        if not pattern:
            return _err("Missing pattern", t0)
        if target not in ("content", "files"):
            return _err(f"Invalid target: {target!r} (expected content|files)", t0)

        # Resolve + guard the search root.
        root = data_root() if path_arg is None else (
            Path(str(path_arg)) if Path(str(path_arg)).is_absolute() else data_root() / str(path_arg)
        )
        if not is_within_root(root):
            return _err("Path traversal denied", t0)
        if not root.exists():
            return _err(f"Search root does not exist: {root}", t0)

        # 2. DECISION — consecutive-identical-search loop detection.
        key = (pattern, target, str(root), file_glob, max_results)
        count = self._tracker.observe(key)
        if count >= _LOOP_THRESHOLD:
            log.tool.info("search_files.execute: loop detected", extra={"_fields": {"count": count}})
            return _ok(
                f"BLOCKED: this exact search has run {count} times in a row and the results "
                "have not changed. Use what you already have, or narrow the pattern/path.",
                t0,
            )

        # 3. STEP — ripgrep when available, else pure-Python walk.
        try:
            if shutil.which("rg") is not None:
                result = await self._rg_search(pattern, target, root, file_glob, max_results)
            else:
                result = self._walk_search(pattern, target, root, file_glob, max_results)
        except re.error as exc:
            return _err(f"Invalid regex pattern: {exc}", t0)
        except Exception as exc:
            return _err(f"search failed: {type(exc).__name__}: {exc}", t0)

        # 4. EXIT — terse rendering with an explicit showing-N-of-M line.
        lines = [f"{m.path}:{m.line}: {m.text}" if target == "content" else m.path for m in result.matches]
        header = f"showing {len(result.matches)} of {result.total} (engine={result.engine})"
        if result.truncated:
            header += " — narrow the pattern or path to see more"
        payload = json.dumps({"summary": header, "results": lines}, ensure_ascii=False)
        return _ok(payload, t0)

    # ------------------------------------------------------------------ engines

    async def _rg_search(
        self, pattern: str, target: str, root: Path, file_glob: str | None, max_results: int,
    ) -> _SearchResult:
        # argv-array only — never shell=True. --no-follow blocks symlink escapes;
        # rg is .gitignore-aware by default.
        if target == "files":
            # Bare file paths, one per line — no colon ambiguity (Windows-safe).
            argv = ["rg", "--files", "--no-follow", "--color=never", "--glob", pattern, str(root)]
        else:
            # --json gives structured path/line/text, so colons in absolute paths
            # (incl. Windows C:\) or match text can never confuse parsing (C1).
            # No --max-count: it caps PER FILE, which corrupts the total/truncated
            # contract — cap in Python so rg and the walk engine agree (M1).
            argv = ["rg", "--json", "--no-follow", "--smart-case"]
            if file_glob:
                argv += ["--glob", file_glob]
            argv += ["--", pattern, str(root)]
        log.tool.debug("search_files.execute: rg", extra={"_fields": {"target": target}})
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=_RG_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            raise
        out = stdout.decode("utf-8", errors="replace")
        res = _SearchResult(engine="rg")
        for raw in out.splitlines():
            if target == "files":
                res.total += 1
                if len(res.matches) < max_results:
                    res.matches.append(_Match(path=self._rel(raw), line=0, text=""))
            else:
                parsed = self._parse_rg_json(raw)
                if parsed is None:
                    continue
                res.total += 1
                if len(res.matches) < max_results:
                    res.matches.append(parsed)
        res.truncated = res.total > len(res.matches)
        return res

    @staticmethod
    def _parse_rg_json(raw: str) -> _Match | None:
        """Parse one `rg --json` event; return a _Match only for type=='match'."""
        try:
            obj = json.loads(raw)
        except ValueError:
            return None
        if obj.get("type") != "match":
            return None
        data = obj.get("data", {})
        path = (data.get("path") or {}).get("text", "")
        text = (data.get("lines") or {}).get("text", "")
        line = data.get("line_number", 0)
        if not path or not isinstance(line, int):
            return None
        return _Match(
            path=SearchFilesTool._rel(path),
            line=line,
            text=text[:_MAX_LINE_LEN].strip(),
        )

    def _walk_search(
        self, pattern: str, target: str, root: Path, file_glob: str | None, max_results: int,
    ) -> _SearchResult:
        """Pure-Python fallback (no rg). Skips dot-directories (incl. .git)."""
        log.tool.debug("search_files.execute: walk fallback")
        res = _SearchResult(engine="walk")
        regex = re.compile(pattern) if target == "content" else None
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune dot-directories in place (language-neutral; covers .git/.venv/etc).
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fname in filenames:
                fpath = Path(dirpath) / fname
                # Confinement per file: os.walk doesn't descend symlinked dirs, but
                # it DOES list symlinked files — a symlink pointing outside the
                # workspace must not be read (M2). is_within_root resolves the link.
                if fpath.is_symlink() and not is_within_root(fpath):
                    continue
                rel_name = fname  # glob target for files mode (basename, like fnmatch)
                if target == "files":
                    if fnmatch.fnmatch(rel_name, pattern):
                        res.total += 1
                        if len(res.matches) < max_results:
                            res.matches.append(_Match(path=self._rel(str(fpath)), line=0, text=""))
                    continue
                if file_glob and not fnmatch.fnmatch(fname, file_glob):
                    continue
                try:
                    with fpath.open("r", encoding="utf-8", errors="ignore") as fh:
                        for lineno, line in enumerate(fh, start=1):
                            if regex is not None and regex.search(line):
                                res.total += 1
                                if len(res.matches) < max_results:
                                    res.matches.append(
                                        _Match(self._rel(str(fpath)), lineno, line[:_MAX_LINE_LEN].strip())
                                    )
                except OSError:
                    continue  # unreadable file — skip, never raise
        res.truncated = res.total > len(res.matches)
        return res

    @staticmethod
    def _rel(p: str) -> str:
        """Render a hit path relative to the workspace root (confinement-safe)."""
        try:
            return str(Path(p).resolve().relative_to(data_root()))
        except ValueError:
            return p

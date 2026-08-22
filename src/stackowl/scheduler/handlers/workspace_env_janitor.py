"""WorkspaceEnvJanitor — reclaim stray per-tool virtualenvs from the workspace.

BAKIR, 2026-08-22: "each tool creates his env instead of using one centralized
env." Measured that morning, the workspace held FOUR virtualenvs — ``genv``
344 MB, ``venv`` 149 MB, ``gmail_env`` 149 MB, ``.venv`` 65 MB — totalling 707 MB
on a root filesystem with 1.3 GB free. ``venv`` and ``gmail_env`` had
byte-identical 24-package sets: one environment, built twice, ten weeks apart.
Nothing in ``src/`` referenced any of them; every one was built ad hoc through
``shell``.

THE CENTRALISED ENV IS THE FIX; THIS IS THE DECAY LEG. `StackowlHome.python_env()`
gives tools one address to install into, and the shell tool now names it. But an
address only redirects NEW work — it reclaims nothing already on disk, and a tool
that legitimately needs isolation (a conflicting dependency version) will still
build its own and then abandon it. Without a sweep, the sprawl returns quietly and
the only symptom is a full disk months later, which is exactly how this one
surfaced: not as "disk full" but as ``database is locked``.

WHAT MAKES A VENV STRAY, and why it is judged on USE rather than age: a venv is
identified structurally (``pyvenv.cfg`` plus an interpreter), never by name — the
four found on the live box were called ``genv``, ``venv``, ``gmail_env`` and
``.venv``, so any name list would have missed the next one. It is reclaimed only
when nothing has TOUCHED it for ``max_idle_days``, measured across the whole tree
rather than on the directory mtime, because a directory's own mtime stops moving
while the env is still being imported from every day.

TWO THINGS IT MUST NEVER REMOVE: the shared env itself, and the project's own
checkout. Both are asserted in tests rather than left to the age check, because
"the janitor deleted the environment everything runs from" is a far worse outcome
than the 707 MB it exists to reclaim.

Mirrors :mod:`downloads_janitor` exactly — same 4-point logging, same
``register_*`` factory, same never-raise contract.
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Iterator
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult

#: Idle days before a stray env is reclaimed. Generous on purpose: rebuilding one
#: costs a download, and a fortnight is long enough that anything still in a
#: rotation has touched it, while `gmail_env` sat unused for over two months.
_DEFAULT_MAX_IDLE_DAYS = 14


def _is_virtualenv(path: Path) -> bool:
    """Structural test, never a name match.

    The four found on the live box were `genv`, `venv`, `gmail_env` and `.venv`;
    a name list would have missed whatever the next one is called.
    """
    if not path.is_dir():
        return False
    if not (path / "pyvenv.cfg").is_file():
        return False
    return (path / "bin" / "python").exists() or (path / "Scripts" / "python.exe").exists()


def _walk_files(root: Path) -> Iterator[Path]:
    """Every regular file under ``root``, WITHOUT following symlinks.

    ``Path.rglob`` descends into symlinked directories, and a virtualenv is full
    of them — the stray ``source`` env on the live box carries ``lib64 -> lib``,
    so rglob walked its library tree twice and reported 104 MB for a directory
    ``du`` measures at 14 MB. Overstating what a sweep freed is the same defect
    this codebase keeps finding in other people's code: trusting the call instead
    of measuring the effect. Following links would also let the walk escape the
    tree entirely.

    ``os.walk`` does not follow directory symlinks by default; files that are
    themselves symlinks are skipped so a link is never counted as its target's
    size (``shutil.rmtree`` removes the link, not the target, so counting the
    target would claim bytes that were never freed).
    """
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        for name in filenames:
            path = base / name
            if path.is_symlink():
                continue
            yield path


def _last_touched(root: Path) -> float:
    """Newest mtime anywhere in the tree, as a POSIX timestamp.

    NOT the directory's own mtime: that stops moving as soon as the env is built,
    while the env goes on being imported from daily. Judging on it would reclaim
    environments that are in active use.
    """
    try:
        newest = root.stat().st_mtime
    except OSError:
        return 0.0
    for path in _walk_files(root):
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def _dir_size(root: Path) -> int:
    """Bytes this sweep would actually free — see :func:`_walk_files`."""
    total = 0
    for path in _walk_files(root):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def find_stray_envs(
    workspace: Path, shared_env: Path, max_idle_days: int
) -> list[tuple[Path, int]]:
    """Virtualenvs under ``workspace`` that are neither shared nor recently used.

    Returns ``(path, size_bytes)`` pairs. Only the workspace's immediate children
    are considered: a venv nested inside a project checkout belongs to that
    project, and reaching into it would be this janitor overstepping its folder
    the way the downloads sweep is careful not to.
    """
    if not workspace.is_dir():
        return []
    cutoff = time.time() - (max_idle_days * 86_400)
    stray: list[tuple[Path, int]] = []
    for child in sorted(workspace.iterdir()):
        try:
            if child.resolve() == shared_env.resolve():
                continue  # NEVER the env everything runs from
        except OSError:
            continue
        if not _is_virtualenv(child):
            continue
        if _last_touched(child) >= cutoff:
            continue
        stray.append((child, _dir_size(child)))
    return stray


class WorkspaceEnvJanitorHandler(JobHandler):
    """Reclaim stray per-tool virtualenvs from the workspace.

    Optional job ``params``: ``{"max_idle_days": 14}``.
    """

    def __init__(self, workspace: Path, shared_env: Path) -> None:
        self._workspace = workspace
        self._shared_env = shared_env

    @property
    def handler_name(self) -> str:
        return "workspace_env_janitor"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        t0 = time.monotonic()
        max_idle_days = int(job.params.get("max_idle_days", _DEFAULT_MAX_IDLE_DAYS))
        log.scheduler.info(
            "[scheduler] workspace_env_janitor.execute: entry",
            extra={"_fields": {
                "job_id": job.job_id,
                "workspace": str(self._workspace),
                "shared_env": str(self._shared_env),
                "max_idle_days": max_idle_days,
            }},
        )

        # 2. DECISION
        stray = find_stray_envs(self._workspace, self._shared_env, max_idle_days)
        if not stray:
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.info(
                "[scheduler] workspace_env_janitor.execute: exit — nothing stray",
                extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
            )
            return JobResult(
                job_id=job.job_id, effect_class="state_change", success=True,
                output="removed=0 freed_bytes=0", error=None, duration_ms=duration_ms,
                metadata={"envs_removed": 0, "freed_bytes": 0},
            )

        # 3. STEP — reclaim, loudly. A tool that sprawled is worth naming: it is
        # the signal that something is still ignoring the shared env, and a silent
        # sweep would hide the very behaviour this is meant to surface.
        removed = 0
        freed = 0
        for path, size in stray:
            try:
                shutil.rmtree(path)
            except OSError as exc:
                log.scheduler.warning(
                    "[scheduler] workspace_env_janitor: rmtree failed",
                    exc_info=exc, extra={"_fields": {"path": str(path)}},
                )
                continue
            removed += 1
            freed += size
            log.scheduler.warning(
                "[scheduler] workspace_env_janitor: reclaimed a stray virtualenv — "
                "something built its own env instead of using the shared one",
                extra={"_fields": {
                    "job_id": job.job_id, "path": str(path),
                    "size_mb": round(size / 1e6, 1),
                    "shared_env": str(self._shared_env),
                }},
            )

        # 4. EXIT
        duration_ms = (time.monotonic() - t0) * 1000
        log.scheduler.info(
            "[scheduler] workspace_env_janitor.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id, "envs_removed": removed,
                "freed_mb": round(freed / 1e6, 1), "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id, effect_class="state_change", success=True,
            output=f"removed={removed} freed_bytes={freed}", error=None,
            duration_ms=duration_ms,
            metadata={"envs_removed": removed, "freed_bytes": freed,
                      "max_idle_days": max_idle_days},
        )


def register_workspace_env_janitor_handler() -> None:
    """Construct + register the janitor on the process registry."""
    from stackowl.paths import StackowlHome

    handler = WorkspaceEnvJanitorHandler(
        workspace=StackowlHome.workspace(),
        shared_env=StackowlHome.python_env(),
    )
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] workspace_env_janitor handler registered",
        extra={"_fields": {"handler": handler.handler_name}},
    )

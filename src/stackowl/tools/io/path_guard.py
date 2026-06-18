"""Shared filesystem path-confinement guard for all file-touching tools.

Every tool that reads, writes, searches, or patches the filesystem confines its
targets to ``StackowlHome.workspace()`` through this single module. It was
extracted from ``read_file.py`` (party E3 condition #1) so the security primitive
lives in exactly ONE place: copy-pasting a confinement check across the read,
write, search, and patch tools is precisely how one copy drifts and becomes the
hole. All E3 file-ops tools import ``is_within_root`` from here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

# An OPTIONAL per-context override of the confinement root. Default None → the guard
# anchors to StackowlHome.workspace() exactly as before (the no-PTC path is byte-for-
# byte unchanged). The E11 PTC channel sets this to the run's SANDBOX workspace via
# :func:`use_root` so that when sandboxed code calls write_file/edit over the
# callback channel, the SAME single confinement primitive (is_within_root /
# resolve_in_workspace) re-anchors to the sandbox scratch — never the host data_root.
_root_override: ContextVar[Path | None] = ContextVar("path_guard_root_override", default=None)


@contextmanager
def use_root(root: Path) -> Iterator[None]:
    """Temporarily re-anchor :func:`data_root` to ``root`` for the current context.

    Used by the PTC server to confine a sandboxed write_file/edit callback to the
    run's sandbox workspace. The override is reset on exit (even on error), so the
    host workspace confinement is restored — nothing leaks across calls.
    """
    token = _root_override.set(root.resolve())
    try:
        yield
    finally:
        _root_override.reset(token)


def data_root() -> Path:
    """The workspace root every file path must resolve inside of.

    Honors a :func:`use_root` context override (the PTC sandbox-workspace anchor)
    when one is active; otherwise the host workspace (the default, unchanged path).
    """
    override = _root_override.get()
    if override is not None:
        return override
    from stackowl.paths import StackowlHome

    return StackowlHome.workspace().resolve()


def resolve_in_workspace(path_str: str) -> Path:
    """Anchor a user-supplied path to the workspace before guarding.

    An ABSOLUTE path is returned unchanged; a RELATIVE path resolves UNDER the
    workspace (:func:`data_root`), NOT the process CWD. This mirrors how
    ``search_files`` anchors its ``path`` arg and what ``search_files`` emits as
    hit paths, so a relative hit piped straight into read/edit/patch round-trips
    correctly. :func:`is_within_root` still confines the result (defense in depth).
    """
    candidate = Path(path_str)
    return candidate if candidate.is_absolute() else data_root() / path_str


def is_within_root(path: Path) -> bool:
    """Return True iff ``path`` resolves to a location inside :func:`data_root`.

    Resolves symlinks/``..`` first, so traversal and symlink escapes are both
    caught. Never raises — a bad/odd path simply returns False.
    """
    try:
        path.resolve().relative_to(data_root())
        return True
    except (ValueError, OSError):
        return False

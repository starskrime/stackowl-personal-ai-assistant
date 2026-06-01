"""Shared filesystem path-confinement guard for all file-touching tools.

Every tool that reads, writes, searches, or patches the filesystem confines its
targets to ``StackowlHome.workspace()`` through this single module. It was
extracted from ``read_file.py`` (party E3 condition #1) so the security primitive
lives in exactly ONE place: copy-pasting a confinement check across the read,
write, search, and patch tools is precisely how one copy drifts and becomes the
hole. All E3 file-ops tools import ``is_within_root`` from here.
"""

from __future__ import annotations

from pathlib import Path


def data_root() -> Path:
    """The workspace root every file path must resolve inside of."""
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

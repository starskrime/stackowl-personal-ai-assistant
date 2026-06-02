"""Write-confinement to the SANDBOX workspace for PTC write_file/edit callbacks.

The five-tool PTC allowlist includes the two WRITE tools (``write_file`` / ``edit``).
Operator decision: those may touch ONLY the run's own sandbox workspace — never the
host project tree, ``~/.stackowl`` secrets, or the agent ``data_root``. The host
``write_file``/``edit`` tools confine to :func:`stackowl.tools.io.path_guard.data_root`
(the agent workspace); here we RE-ANCHOR that single confinement primitive to the
sandbox scratch for the duration of one PTC write, via the
:func:`stackowl.tools.io.path_guard.use_root` context override.

Defense in depth: BEFORE invoking the tool we ALSO independently resolve the target
(symlinks + ``..`` collapsed) and reject anything escaping the sandbox workspace — so
even if the tool's own guard were bypassed, the escape is refused here first. The
sandbox is never trusted to send a confined path; the host enforces it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from stackowl.paths import StackowlHome
from stackowl.tools.io.path_guard import data_root, use_root

__all__ = ["confined_path_arg", "read_target_protected", "sandbox_write_root"]


def _protected_read_roots() -> list[Path]:
    """The internal data stores PTC ``read_file`` must NOT bulk-read from a sandbox.

    ``memory`` (allowlisted) already exposes CURATED semantic recall; raw ``read_file``
    of these would let a sandbox exfiltrate the ENTIRE conversation DB / vector store /
    knowledge graph (and skills/tools internals) in a single call — materially worse
    than query-by-query recall. Secrets live OUTSIDE the workspace but are included
    defensively. An unresolvable store path is simply skipped.
    """
    h = StackowlHome
    roots: list[Path] = []
    for getter in (
        h.db_path, h.lancedb_dir, h.kuzu_dir, h.skills_dir, h.knowledge_dir,
        h.tools_dir, h.learned_tools_dir, h.providers_dir, h.secrets_dir,
    ):
        try:
            roots.append(getter().resolve())
        except (OSError, ValueError):  # pragma: no cover — defensive
            continue
    return roots


def read_target_protected(args: dict[str, object]) -> bool:
    """True iff the request's read ``path`` resolves INTO a protected internal store.

    Resolves the path exactly as ``read_file`` does (relative to the current
    ``data_root``), then checks containment against the protected store roots. Fail
    CLOSED: a present-but-unresolvable path → True (refuse). A missing path → False
    (the tool itself rejects it). Never raises.
    """
    raw = args.get("path")
    if not isinstance(raw, str) or not raw:
        return False
    try:
        candidate = Path(raw)
        anchored = candidate if candidate.is_absolute() else data_root() / raw
        target = anchored.resolve()
    except (ValueError, OSError):
        return True  # present-but-unresolvable → fail-closed
    for root in _protected_read_roots():
        if target == root:
            return True
        try:
            target.relative_to(root)
            return True
        except ValueError:
            continue
    return False


@contextmanager
def sandbox_write_root(workspace: Path) -> Iterator[None]:
    """Re-anchor path_guard's confinement to the SANDBOX ``workspace`` for this block.

    Within the block, the host ``write_file``/``edit`` tools confine to ``workspace``
    (the run's sandbox scratch) instead of the agent data_root. Reset on exit.
    """
    with use_root(workspace):
        yield


def confined_path_arg(args: dict[str, object], workspace: Path) -> Path | None:
    """Return the request's ``path`` resolved INSIDE ``workspace``, or None if it escapes.

    Anchors a relative path under the sandbox ``workspace`` and resolves symlinks/``..``
    on the result, then verifies it stays inside the (resolved) workspace. Returns the
    safe absolute path on success; ``None`` when the path is missing, malformed, or
    escapes — the caller refuses without invoking the tool. Never raises.
    """
    raw = args.get("path")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        root = workspace.resolve()
        candidate = Path(raw)
        anchored = candidate if candidate.is_absolute() else root / raw
        resolved = anchored.resolve()
        resolved.relative_to(root)
    except (ValueError, OSError):
        return None
    return resolved

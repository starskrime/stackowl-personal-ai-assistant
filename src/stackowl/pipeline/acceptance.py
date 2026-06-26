"""AcceptanceChecker — the ONE goal-level acceptance authority (verification B3).

The goal-level half of the verification primitive. Where ``ToolResult.verified``
(B1/B2) measures whether ONE tool's own artifact is real, the AcceptanceChecker
measures whether a turn's DECLARED post-condition (:class:`ExpectedOutcome`) is
real — observed against the filesystem after the turn runs. This catches the
class the per-tool net cannot: a tool that exits 0 without producing anything and
*cannot self-verify* (e.g. ``shell`` running ``yt-dlp --simulate``), because the
turn declared the expected artifact UP FRONT rather than asking the tool to know
its own contract.

Two layers, mirroring the spec:

* **Deterministic (here, always on when a criterion is declared):** observe the
  declared artifact directory for a FRESH non-empty file. No model, no per-site or
  keyword logic — pure filesystem observation with the same freshness/existence
  semantics as :func:`stackowl.tools.verification.verify_artifact`.
* **LLM-derived (separate, flag-OFF, fail-closed):** lives in
  :mod:`stackowl.pipeline.acceptance_llm`; derives an ExpectedOutcome only when one
  was not declared, post-hoc, and never asserts a positive pass it could not
  measure. Engaged only behind ``settings.acceptance_tier`` (default OFF).

The checker NEVER raises and NEVER flips a real success to a failure on an
inability to observe reality (a filesystem error ⇒ ``accepted=None`` ⇒ no opinion).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.objectives.model import ExpectedOutcome

# Same granularity slack as verify_artifact: a file written DURING the turn must
# never read as stale due to coarse (≥1s) mtime rounding. A real stale artifact (a
# previous run's file) is seconds-to-minutes old and still fails comfortably.
_MTIME_TOLERANCE_S = 2.0

# Upper bound on directory entries scanned before giving up — a backstop against an
# enormous declared directory. Far above any real artifact-output dir; the first
# fresh file short-circuits long before this.
_MAX_SCAN_ENTRIES = 50_000


@dataclass(frozen=True)
class AcceptanceVerdict:
    """The acceptance decision. ``accepted`` is tri-state, mirroring ``verified``:

    * ``None`` — not engaged / no opinion (no declared outcome, or reality could
      not be observed). The caller falls back to its prior signal (byte-identical).
    * ``True`` — the declared post-condition was observed.
    * ``False`` — the post-condition was declared but reality refuted it: the turn
      claimed an outcome it did not produce.
    """

    accepted: bool | None
    reason: str


def _resolve_dir(artifact_dir: str | None) -> Path:
    """Resolve the declared directory: absolute as-is; relative under the workspace;
    None ⇒ the workspace root. Mirrors ``resolve_in_workspace`` without coupling the
    pipeline to the file-ops tools (this is a read-only observation, not a write)."""
    from stackowl.paths import StackowlHome

    root = StackowlHome.workspace().resolve()
    if not artifact_dir:
        return root
    candidate = Path(artifact_dir)
    return candidate if candidate.is_absolute() else root / artifact_dir


def _dir_has_fresh_file(directory: Path, not_before: float) -> bool | None:
    """True iff ``directory`` contains a regular, non-empty file whose mtime is at or
    after ``not_before`` (this turn's start). False iff the directory is absent or
    holds no such file. None on a filesystem error (no opinion). Never raises.

    A recursive, bounded scan — an artifact may land in a sub-directory the planner
    did not name (e.g. a per-item download folder)."""
    try:
        if not directory.is_dir():
            return False
        scanned = 0
        stack = [directory]
        while stack:
            current = stack.pop()
            with os.scandir(current) as it:
                for entry in it:
                    scanned += 1
                    if scanned > _MAX_SCAN_ENTRIES:
                        return False
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        st = entry.stat(follow_symlinks=False)
                        if st.st_size > 0 and st.st_mtime >= not_before - _MTIME_TOLERANCE_S:
                            return True
                    except OSError:
                        continue  # one bad entry must not sink the scan
        return False
    except OSError:
        return None


class AcceptanceChecker:
    """Observe a declared :class:`ExpectedOutcome` against reality. Stateless."""

    def check(
        self,
        outcome: ExpectedOutcome | None,
        *,
        turn_started_at: float,
        acted: bool,
    ) -> AcceptanceVerdict:
        """Return the acceptance verdict for a turn.

        ``turn_started_at`` is epoch seconds captured BEFORE the turn ran (the
        freshness clock). ``acted`` is the behavior gate: the turn did something
        (produced a response or dispatched a tool). A turn that did literally
        nothing is never penalized — it had no chance to produce an outcome — which
        keeps a conversational/no-op turn byte-identical.
        """
        if outcome is None or outcome.kind == "none":
            return AcceptanceVerdict(None, "no declared outcome")
        if not acted:
            return AcceptanceVerdict(None, "turn took no action — not engaged")
        if outcome.kind == "artifact":
            observed = _dir_has_fresh_file(_resolve_dir(outcome.artifact_dir), turn_started_at)
            if observed is None:
                log.engine.debug(
                    "[acceptance] artifact directory unobservable — no opinion",
                    extra={"_fields": {"artifact_dir": outcome.artifact_dir}},
                )
                return AcceptanceVerdict(None, "artifact directory could not be observed")
            if observed:
                return AcceptanceVerdict(True, "fresh artifact observed")
            return AcceptanceVerdict(
                False,
                f"declared an artifact under {outcome.artifact_dir or 'the workspace'} "
                "but no fresh file was produced",
            )
        return AcceptanceVerdict(None, f"unknown outcome kind: {outcome.kind}")

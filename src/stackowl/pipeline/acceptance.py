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
But "could not observe a DECLARED outcome" is no longer indistinguishable from
"nothing was declared": the former carries ``unobservable=True`` (F-13) so a caller
can treat it as a soft-fail / retry signal rather than an implicit pass.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.objectives.model import ExpectedOutcome

if TYPE_CHECKING:
    from stackowl.pipeline.acceptance_authority import AcceptanceAuthority

# Same granularity slack as verify_artifact: a file written DURING the turn must
# never read as stale due to coarse (≥1s) mtime rounding. A real stale artifact (a
# previous run's file) is seconds-to-minutes old and still fails comfortably.
_MTIME_TOLERANCE_S = 2.0

# Upper bound on directory entries scanned before giving up — a backstop against an
# enormous declared directory. Far above any real artifact-output dir; the first
# fresh file short-circuits long before this.
_MAX_SCAN_ENTRIES = 50_000

# Re-probe timeout (seconds) for the HTTP observable kind. Short — a verification
# re-probe must never become its own latency source.
_HTTP_PROBE_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class HttpProbeOutcome:
    """A GENERAL side-effect re-probe post-condition (verification B3, F-12).

    Where :class:`ExpectedOutcome` (``kind="artifact"``) observes the FILESYSTEM,
    this observes the NETWORK: after the turn ran, re-fetch ``url`` and require a
    success/redirect (2xx/3xx) status. Any effect that publishes a verifiable
    endpoint — a deployed service, an uploaded object's public URL, a created
    record's API resource — maps onto this one kind. It is deliberately
    vendor-neutral: only a URL and a status-class check, no provider specifics.

    The re-probe itself is a pluggable :class:`HttpProber` (the checker's default
    issues a short-timeout ``HEAD``); a refuted/absent endpoint yields ``False``,
    an unreachable one yields ``None`` (no opinion — never a fabricated failure).
    """

    url: str
    kind: str = "http"


#: A re-probe function: given a URL, return True (live / 2xx-3xx), False (observed
#: error response, e.g. 4xx/5xx), or None (could not observe — no opinion).
HttpProber = Callable[[str], "bool | None"]


def _default_http_prober(url: str) -> bool | None:
    """Issue a short-timeout HEAD and classify the result. Never raises.

    True for a 2xx/3xx status; False for an observed 4xx/5xx error response; None on
    any transport/timeout error (the endpoint could not be observed — no opinion, so
    a real success is never flipped to a failure on our inability to reach it).

    NOTE (deferral): callers wire the URL from a DECLARED/DERIVED outcome, not raw
    user input; if a future producer lets untrusted text reach this prober, an
    SSRF-hardening allowlist belongs here. No live populator exists today.
    """
    try:
        req = urllib.request.Request(url, method="HEAD")  # noqa: S310 — declared outcome URL
        with urllib.request.urlopen(req, timeout=_HTTP_PROBE_TIMEOUT_S) as resp:  # noqa: S310
            status = getattr(resp, "status", None)
            return None if status is None else 200 <= status < 400
    except urllib.error.HTTPError as exc:
        # A real HTTP error response WAS observed (4xx/5xx) — the effect is refuted.
        return 200 <= exc.code < 400
    except Exception as exc:  # noqa: BLE001 — unreachable ⇒ no opinion, never raise
        log.engine.debug(
            "[acceptance] http re-probe unreachable — no opinion",
            extra={"_fields": {"err": type(exc).__name__}},
        )
        return None


@dataclass(frozen=True)
class AcceptanceVerdict:
    """The acceptance decision. ``accepted`` is tri-state, mirroring ``verified``:

    * ``None`` — not engaged / no opinion (no declared outcome, or reality could
      not be observed). The caller falls back to its prior signal (byte-identical).
    * ``True`` — the declared post-condition was observed.
    * ``False`` — the post-condition was declared but reality refuted it: the turn
      claimed an outcome it did not produce.

    ``unobservable`` (F-13) disambiguates the two ``accepted is None`` cases that
    were previously indistinguishable to a caller:

    * ``unobservable is False`` — there was nothing to judge (no declared outcome,
      or the turn took no action). A genuine no-op skip.
    * ``unobservable is True`` — a post-condition WAS declared, but reality could
      not be OBSERVED (a filesystem error / an unreachable endpoint). This is NOT
      an implicit pass: the inability to observe a declared consequential outcome
      is a distinct soft-fail signal a caller may treat as retry-worthy, rather than
      silently falling back to the turn's self-asserted success. We still never
      fabricate a failure (``accepted`` stays ``None``), but the caller can now SEE
      that "we could not verify" rather than mistaking it for "nothing to verify".
    """

    accepted: bool | None
    reason: str
    unobservable: bool = False

    @property
    def engaged(self) -> bool:
        """True iff the checker actually attempted to OBSERVE a declared outcome
        (it passed, was refuted, or was unobservable) — as opposed to being skipped
        because nothing was declared or the turn took no action. Lets a caller trace
        "acceptance run" vs "acceptance skipped" explicitly (F-1 explainability),
        instead of inferring it from ``accepted is None`` (which conflates the two).
        """
        return self.accepted is not None or self.unobservable


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
    """Observe a declared post-condition against reality.

    Handles the FILESYSTEM kind (:class:`ExpectedOutcome` ``kind="artifact"``) and
    the NETWORK kind (:class:`HttpProbeOutcome`). ``http_prober`` is injectable so
    the network re-probe is unit-testable without touching the wire; the default
    issues a short-timeout HEAD. Any unrecognized kind yields a clear no-opinion
    verdict (never raises)."""

    def __init__(self, *, http_prober: HttpProber | None = None) -> None:
        self._http_prober: HttpProber = http_prober or _default_http_prober

    def check(
        self,
        outcome: ExpectedOutcome | HttpProbeOutcome | None,
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
        if outcome is None:
            return AcceptanceVerdict(None, "no declared outcome")
        # An ExpectedOutcome with kind "none" is "no declared outcome" — the no-op.
        if isinstance(outcome, ExpectedOutcome) and outcome.kind == "none":
            return AcceptanceVerdict(None, "no declared outcome")
        if not acted:
            return AcceptanceVerdict(None, "turn took no action — not engaged")
        if isinstance(outcome, ExpectedOutcome):
            if outcome.kind == "artifact":
                return self._check_artifact(outcome, turn_started_at)
            return AcceptanceVerdict(None, f"unknown outcome kind: {outcome.kind}")
        if isinstance(outcome, HttpProbeOutcome):
            return self._check_http(outcome)
        return AcceptanceVerdict(
            None, f"unknown outcome kind: {getattr(outcome, 'kind', type(outcome).__name__)}"
        )

    def _check_artifact(
        self, outcome: ExpectedOutcome, turn_started_at: float
    ) -> AcceptanceVerdict:
        # ADR-1 — delegate the OBSERVATION to the single AcceptanceAuthority (directory
        # mode), then adapt its tri-state Verdict to this checker's AcceptanceVerdict.
        # The goal-level checker keeps its own RESPONSE (the F-13 unobservable reason text)
        # but no longer owns a private copy of the probe. Local import avoids the
        # acceptance ⇆ acceptance_authority module cycle.
        from stackowl.pipeline.acceptance_authority import ArtifactFresh

        verdict = self._authority().observe(
            ArtifactFresh(artifact_dir=outcome.artifact_dir),
            success=True,
            started_at=turn_started_at,
        )
        if verdict.accepted is None:
            log.engine.debug(
                "[acceptance] artifact directory unobservable — no opinion",
                extra={"_fields": {"artifact_dir": outcome.artifact_dir}},
            )
            return AcceptanceVerdict(
                None, "artifact directory could not be observed", unobservable=True
            )
        if verdict.accepted:
            return AcceptanceVerdict(True, "fresh artifact observed")
        return AcceptanceVerdict(
            False,
            f"declared an artifact under {outcome.artifact_dir or 'the workspace'} "
            "but no fresh file was produced",
        )

    def _check_http(self, outcome: HttpProbeOutcome) -> AcceptanceVerdict:
        from stackowl.pipeline.acceptance_authority import HttpOk

        verdict = self._authority().observe(HttpOk(url=outcome.url), success=True)
        if verdict.accepted is None:
            log.engine.debug(
                "[acceptance] endpoint unobservable — no opinion",
                extra={"_fields": {"url": outcome.url}},
            )
            return AcceptanceVerdict(
                None, "endpoint could not be probed", unobservable=True
            )
        if verdict.accepted:
            return AcceptanceVerdict(True, "declared endpoint responded successfully")
        return AcceptanceVerdict(
            False,
            f"declared endpoint {outcome.url} did not respond successfully",
        )

    def _authority(self) -> AcceptanceAuthority:
        """Build an AcceptanceAuthority that shares THIS checker's injected
        ``http_prober`` (so the network re-probe stays unit-testable). Local import
        avoids the module-load cycle (the authority imports this module's primitives)."""
        from stackowl.pipeline.acceptance_authority import AcceptanceAuthority

        return AcceptanceAuthority(http_prober=self._http_prober)

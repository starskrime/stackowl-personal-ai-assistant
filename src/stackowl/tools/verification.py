"""Verification primitive — measure whether a tool's claimed effect is real.

The platform's foundational honesty bit. ``ToolResult.success`` is the tool's
SELF-REPORT; ``ToolResult.verified`` is the reality check. This module holds the
two pieces every layer reads:

* :func:`is_trustworthy_success` — the ONE derived predicate. ``verified is None``
  falls back to ``success`` (byte-identical to pre-verification behavior); a
  ``verified is False`` (claimed-but-absent) is never a trustworthy win.
* :func:`verify_artifact` — the hardened existence oracle a tool's ``verify()``
  calls: existence + non-empty + FRESHNESS (this run's artifact, not a stale
  predictable-path file from a previous run) + an optional magic-byte/MIME sanity
  check (rejects a tiny error page saved with a media extension). Content
  *correctness* (right subject) is a documented ceiling, out of scope here.

General and vendor-neutral: the signature table is binary FORMAT facts (not a
language/keyword list), the freshness clock is the caller's, no per-site logic.
"""

from __future__ import annotations

from pathlib import Path

# Filesystem mtime can be coarse (≥1s granularity on some backends). A file
# written DURING the call must never read as stale due to rounding, so the
# freshness comparison allows this slack. Real stale artifacts (a previous run's
# file) are seconds-to-minutes old and still fail comfortably.
_MTIME_TOLERANCE_S = 2.0

# Magic-byte signatures by artifact KIND. Each entry is (offset, signature-bytes).
# A header matches the kind if ANY of its signatures match at its offset. These
# are objective binary format markers, not a locale/keyword list. An unknown kind
# is "no opinion" (never fails a file we cannot judge).
_SIGNATURES: dict[str, tuple[tuple[int, bytes], ...]] = {
    "image": (
        (0, b"\x89PNG\r\n\x1a\n"),     # PNG
        (0, b"\xff\xd8\xff"),           # JPEG
        (0, b"GIF87a"),                 # GIF
        (0, b"GIF89a"),
        (0, b"BM"),                     # BMP
        (8, b"WEBP"),                   # WEBP (RIFF....WEBP)
    ),
    "audio": (
        (0, b"ID3"),                    # MP3 w/ ID3 tag
        (0, b"\xff\xfb"),               # MP3 frame
        (0, b"\xff\xf3"),
        (0, b"\xff\xf2"),
        (0, b"OggS"),                   # OGG
        (0, b"fLaC"),                   # FLAC
        (8, b"WAVE"),                   # WAV (RIFF....WAVE)
        (4, b"ftyp"),                   # M4A / MP4 audio
    ),
    "pdf": (
        (0, b"%PDF"),
    ),
}

# Bytes to read for the header check — enough to cover every signature offset+len.
_HEADER_READ = 16


def is_trustworthy_success(success: bool, verified: bool | None) -> bool:
    """The ONE predicate every decider reads.

    ``verified is None`` ⇒ not checked ⇒ fall back to ``success`` (byte-identical).
    ``verified is False`` ⇒ claimed-but-not-observed ⇒ never trustworthy.
    """
    return success and verified is not False


def _matches_kind(header: bytes, expect_kind: str) -> bool:
    """True if ``header`` matches any signature for ``expect_kind`` — or the kind
    is unknown to us (no opinion; do not fail an artifact we cannot judge)."""
    sigs = _SIGNATURES.get(expect_kind)
    if sigs is None:
        return True
    return any(header[off:off + len(sig)] == sig for off, sig in sigs)


def verify_artifact(
    path: str | Path | None,
    *,
    not_before: float | None = None,
    expect_kind: str | None = None,
) -> bool | None:
    """Observe whether ``path`` is a real, fresh, non-empty artifact.

    Returns ``True`` (observed), ``False`` (claimed but absent / empty / stale /
    wrong-format), or ``None`` (no opinion — ``path`` is empty, or the filesystem
    could not be observed). Never raises.

    * **existence + non-empty** — a regular file with ``size > 0``.
    * **freshness** — when ``not_before`` is given (the tool-call start time, epoch
      seconds), the file's mtime must be at/after it (minus a small granularity
      slack), so a stale predictable-path file from a previous run cannot pass.
    * **magic-byte** — when ``expect_kind`` is given, the header must match a known
      signature for that kind; a tiny error page saved as media is rejected.
    """
    if not path:
        return None
    p = Path(path)
    try:
        if not p.is_file():
            return False
        st = p.stat()
        if st.st_size == 0:
            return False
        if not_before is not None and st.st_mtime < not_before - _MTIME_TOLERANCE_S:
            return False
        if expect_kind is not None:
            with p.open("rb") as fh:
                header = fh.read(_HEADER_READ)
            if not _matches_kind(header, expect_kind):
                return False
        return True
    except OSError:
        # Could not observe reality (transient FS error) → no opinion, never flip a
        # real success to a failure on an inability to check.
        return None

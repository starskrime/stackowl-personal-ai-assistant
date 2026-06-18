"""install_remote_plugin — verified remote plugin install (PLUG-2).

Flow (security order is the whole point):
  1. REFUSE up-front if the index entry carries no integrity digest (unverifiable).
  2. DOWNLOAD the archive bytes via an INJECTED downloader (no network in tests; the
     real downloader is a bounded urllib fetch). The bytes are DATA — never executed.
  3. VERIFY the bytes' SHA-256 against the index digest (``PluginVerifier``,
     fail-closed). A mismatch raises and nothing is written.
  4. EXTRACT the verified zip into a temp dir with a zip-slip guard (no path escapes
     the extraction root). Still no code execution.
  5. INSTALL via the SAME consent-gated local path (``_install_local_plugin``), which
     records the verified digest. Third-party code only ever runs later, at ``serve``
     boot, behind the same consent the user already granted — never as a side effect
     of downloading.
"""

from __future__ import annotations

import logging
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

from stackowl.exceptions import PluginValidationError
from stackowl.plugins.index import PluginIndexEntry
from stackowl.plugins.verify import PluginVerificationError, PluginVerifier

log = logging.getLogger("stackowl.plugins")

# An injected downloader maps a URL → archive bytes. Kept abstract so tests pass a
# pure in-memory blob and production passes a bounded urllib fetch.
Downloader = Callable[[str], bytes]

# Defensive bound — a remote plugin archive larger than this is refused (a DoS /
# zip-bomb guard before extraction). Generous for real plugins, far below memory.
_MAX_ARCHIVE_BYTES = 50 * 1024 * 1024


def _default_downloader(url: str) -> bytes:
    """Bounded urllib GET. Reads at most ``_MAX_ARCHIVE_BYTES + 1`` then refuses."""
    import urllib.request

    log.debug(
        "plugins.remote_install._default_downloader: entry",
        extra={"_fields": {"url_origin": _origin(url)}},
    )
    # Scheme allow-list BEFORE opening (no file://, ftp://, data:, etc.).
    if not url.lower().startswith(("https://", "http://")):
        raise PluginVerificationError(f"unsupported URL scheme: {_origin(url)}")
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 — scheme checked above
        data = bytes(resp.read(_MAX_ARCHIVE_BYTES + 1))
    if len(data) > _MAX_ARCHIVE_BYTES:
        raise PluginVerificationError("remote plugin archive exceeds size limit")
    return data


def _origin(url: str) -> str:
    """Log-safe URL (origin + path only, never query strings that may carry keys)."""
    try:
        from urllib.parse import urlparse

        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}{p.path}"
    except Exception:  # noqa: BLE001
        return "<unparseable-url>"


def _safe_extract(zip_bytes: bytes, dest: Path) -> Path:
    """Extract a verified zip into *dest* with a zip-slip guard. Return plugin root.

    Refuses any member whose resolved path escapes *dest* (absolute paths, ``..``).
    Returns the directory that directly contains ``plugin.yaml``.
    """
    import io

    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            member = info.filename
            # Reject symlink members fail-closed: a stored symlink lets a
            # follow-up member write THROUGH it to escape the root, so the
            # path-containment check below (which only sees stored names) is
            # not enough. The Unix file-type bits live in the high 16 bits of
            # external_attr; 0o120000 marks a symlink.
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise PluginVerificationError(
                    f"refusing symlink member in plugin archive: {member!r}"
                )
            target = (dest / member).resolve()
            # Proper containment check — `startswith` accepts a prefix-collision
            # sibling (e.g. dest=/x/root accepts /x/rootX/evil). is_relative_to
            # compares path components, not string prefixes (Python 3.9+).
            if target != dest_resolved and not target.is_relative_to(dest_resolved):
                raise PluginVerificationError(
                    f"refusing zip member that escapes extraction root: {member!r}"
                )
        zf.extractall(dest)  # noqa: S202 — guarded above against path traversal
    # Locate the plugin.yaml (top-level or one dir down).
    matches = list(dest.rglob("plugin.yaml"))
    if not matches:
        raise PluginValidationError(str(dest), "archive has no plugin.yaml")
    return matches[0].parent


def install_remote_plugin(
    entry: PluginIndexEntry,
    *,
    consent_granted: bool,
    db_path: Path,
    downloader: Downloader | None = None,
    verifier: PluginVerifier | None = None,
) -> str:
    """Verify and install a remote plugin from an index *entry*. Returns its name.

    Raises ``PluginVerificationError`` (fail-closed) on a missing/mismatched digest,
    ``PermissionError`` when consent was not granted, ``PluginValidationError`` for a
    bad manifest. NEVER installs or executes an unverified download.
    """
    log.debug(
        "plugins.remote_install.install_remote_plugin: entry",
        extra={"_fields": {"name": entry.name, "url_origin": _origin(entry.url)}},
    )
    dl = downloader or _default_downloader
    verify = verifier or PluginVerifier()

    # 1. REFUSE early when there is nothing to verify against (fail-closed).
    if not (entry.sha256 or "").strip():
        log.warning(
            "plugins.remote_install: refused — index entry has no sha256",
            extra={"_fields": {"name": entry.name}},
        )
        raise PluginVerificationError(
            f"plugin {entry.name!r} has no sha256 in the index — refusing to install "
            "an unverifiable remote download (fail-closed)"
        )

    # 2. DOWNLOAD (bytes are inert data — never executed).
    archive = dl(entry.url)

    # 3. VERIFY before touching disk. A mismatch raises → nothing installed.
    verified_digest = verify.verify_bytes(archive, expected_sha256=entry.sha256)

    # 4. EXTRACT (verified) into a temp staging dir with a zip-slip guard.
    from stackowl.cli.app import _install_local_plugin

    staging = Path(tempfile.mkdtemp(prefix="stackowl-plugin-"))
    try:
        plugin_root = _safe_extract(archive, staging)
        # 5. INSTALL via the consent-gated local path; records the verified digest.
        name = _install_local_plugin(
            plugin_root,
            consent_granted=consent_granted,
            db_path=db_path,
            sha256=verified_digest,
        )
    finally:
        import shutil

        shutil.rmtree(staging, ignore_errors=True)
    log.info(
        "plugins.remote_install.install_remote_plugin: exit — installed",
        extra={"_fields": {"name": name, "sha256": verified_digest}},
    )
    return name

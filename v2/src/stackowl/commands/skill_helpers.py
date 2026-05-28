"""Helpers for ``/skill`` slash command — install (path/git/tar/zip), hashing,
conflict resolution, audit dispatch.

Kept separate from :class:`SkillCommand` so command stays readable and the
heavier install/extract logic is independently testable.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import yaml

from stackowl.infra.observability import log
from stackowl.skills.loader import LoadedSkill, SkillLoader
from stackowl.skills.manifest import SkillSource
from stackowl.skills.skill_md import parse_skill_md
from stackowl.skills.store import SkillIndexStore

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.embeddings.registry import EmbeddingRegistry

# Hard ceilings to defend against zip bombs and pathological downloads.
_MAX_ARCHIVE_BYTES = 50 * 1024 * 1024  # 50 MiB
_MAX_EXTRACTED_BYTES = 200 * 1024 * 1024  # 200 MiB total after decompression
_DOWNLOAD_TIMEOUT_S = 60.0
_GIT_CLONE_TIMEOUT_S = 120.0


class SkillInstallError(Exception):
    """Raised when an install fails for a user-facing reason."""


@dataclass(frozen=True)
class InstallResult:
    """Outcome of an install — directory path + final (post-rename) name."""

    name: str
    path: Path


_SNAPSHOT_CAP_BYTES = 256 * 1024  # 256 KiB total per audit entry
_SNAPSHOT_TEXT_EXTENSIONS = {
    ".md", ".yaml", ".yml", ".json", ".toml", ".txt", ".py", ".j2",
    ".sh", ".bash", ".env", ".cfg", ".ini",
}


def snapshot_dir(path: Path) -> dict[str, str]:
    """Return a {relative_path → text-content} dict for every text file under
    ``path``, capped at :data:`_SNAPSHOT_CAP_BYTES` total.

    Per operator vote in Commit 3 sub-phase 3e: snapshot the whole tree (so
    /skill restore can reproduce sidecars too), but bail out to "SKILL.md only"
    when the total exceeds the cap so audit rows don't explode. Binary files
    are silently skipped (we don't try to base64 — restore for those is
    out of scope for this phase; user can re-install if they had assets).
    """
    if not path.exists():
        return {}
    files: list[tuple[str, str]] = []
    total = 0
    for fp in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = fp.relative_to(path).as_posix()
        if fp.suffix.lower() not in _SNAPSHOT_TEXT_EXTENSIONS:
            continue
        try:
            text = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        size = len(text.encode("utf-8"))
        if total + size > _SNAPSHOT_CAP_BYTES:
            # Fallback per operator vote — snapshot just SKILL.md.
            skill_md = path / "SKILL.md"
            if skill_md.exists():
                try:
                    return {"SKILL.md": skill_md.read_text(encoding="utf-8")}
                except (OSError, UnicodeDecodeError):
                    return {}
            return {}
        files.append((rel, text))
        total += size
    return dict(files)


def restore_snapshot(target_dir: Path, snapshot: dict[str, str]) -> None:
    """Replace ``target_dir`` contents with the files described in ``snapshot``.

    Safe-write: stage in a tempdir alongside the target, then atomic-swap.
    Files not present in the snapshot are deleted (matching restore semantics —
    user wants the tree EXACTLY as it was at audit time).
    """
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    # Build the new tree in a sibling staging dir first.
    staging = target_dir.parent / f".{target_dir.name}.restore-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for rel, content in snapshot.items():
        # Defensive — reject anything that tries to escape the staging root.
        rel_path = Path(rel)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            log.skills.warning(
                "[skills] restore_snapshot: rejecting path-traversal entry",
                extra={"_fields": {"rel": rel}},
            )
            continue
        out = staging / rel_path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
    # Swap.
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.move(str(staging), str(target_dir))


def hash_dir(path: Path) -> str:
    """Stable SHA256 digest over every file (path + content) under ``path``.

    Order-independent within a sort, ignores empty dirs. Used for audit
    before_hash / after_hash so ``/skill diff`` can detect changes.
    """
    h = hashlib.sha256()
    if not path.exists():
        return h.hexdigest()
    for file_path in sorted(p for p in path.rglob("*") if p.is_file()):
        try:
            rel = file_path.relative_to(path).as_posix()
            h.update(rel.encode("utf-8"))
            h.update(b"\x00")
            h.update(file_path.read_bytes())
            h.update(b"\xff")
        except OSError as exc:  # B5
            log.skills.warning(
                "[skills] hash_dir: read failed — skipping file",
                exc_info=exc,
                extra={"_fields": {"file": str(file_path)}},
            )
    return h.hexdigest()


def rewrite_skill_md_name(skill_dir: Path, new_name: str) -> None:
    """Rewrite the ``name:`` field in ``<skill_dir>/SKILL.md`` to ``new_name``.

    Called after a conflict-rename so the manifest stored inside the file
    matches the directory's new name. Without this the loader upserts on
    ``(source, name)`` UNIQUE and a second install with the same frontmatter
    name silently overwrites the first row, leaving the conflict-renamed
    directory invisible to ``/skill list``.

    No-op if the file's frontmatter already has the right name (which means
    a re-call after the rewrite is idempotent).
    """
    # 1. ENTRY
    log.skills.debug(
        "[skills] rewrite_skill_md_name: entry",
        extra={"_fields": {"dir": str(skill_dir), "new_name": new_name}},
    )
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return
    text = skill_md.read_text(encoding="utf-8")
    parsed = parse_skill_md(text)
    current = parsed.frontmatter.get("name")
    if current == new_name:
        log.skills.debug(
            "[skills] rewrite_skill_md_name: exit — already matches",
            extra={"_fields": {"name": new_name}},
        )
        return
    # 3. STEP — emit a new SKILL.md with name updated
    new_fm = dict(parsed.frontmatter)
    new_fm["name"] = new_name
    fm_yaml = yaml.safe_dump(new_fm, sort_keys=False).rstrip("\n")
    new_text = f"---\n{fm_yaml}\n---\n\n{parsed.body}\n"
    skill_md.write_text(new_text, encoding="utf-8")
    # 4. EXIT
    log.skills.info(
        "[skills] rewrite_skill_md_name: rewrote",
        extra={"_fields": {"file": str(skill_md), "from": current, "to": new_name}},
    )


def _read_manifest_name(src: Path) -> str | None:
    """Best-effort: pull the ``name:`` field out of ``<src>/SKILL.md``.

    Returns ``None`` if the file is missing/unparseable so the caller can
    fall back to a sensible default (e.g. source dir name).
    """
    skill_md = src / "SKILL.md"
    if not skill_md.exists():
        return None
    try:
        parsed = parse_skill_md(skill_md.read_text(encoding="utf-8"))
    except Exception as exc:  # B5 — best-effort
        log.skills.debug(
            "[skills] _read_manifest_name: parse failed",
            exc_info=exc, extra={"_fields": {"path": str(skill_md)}},
        )
        return None
    name = parsed.frontmatter.get("name")
    return str(name) if isinstance(name, str) else None


def resolve_install_name(
    source_dir: Path, requested_name: str,
) -> str:
    """Pick a final on-disk name avoiding collisions.

    Per operator vote: auto-rename with ``-1``, ``-2``, ... suffix when
    ``<source_dir>/<requested_name>`` already exists. Never overwrites a
    pre-existing skill.
    """
    if not (source_dir / requested_name).exists():
        return requested_name
    i = 1
    while (source_dir / f"{requested_name}-{i}").exists():
        i += 1
    return f"{requested_name}-{i}"


async def install_from_local_path(
    src: Path, skills_root: Path, *, target_source: SkillSource = "installed",
) -> InstallResult:
    """Copy a local skill directory into ``<skills_root>/<target_source>/<name>/``.

    Validates that ``src`` has a top-level ``SKILL.md`` before copying.
    """
    # 1. ENTRY
    log.skills.info(
        "[skills] install_local: entry",
        extra={"_fields": {"src": str(src), "target_source": target_source}},
    )
    if not src.is_dir():
        raise SkillInstallError(f"source path is not a directory: {src}")
    if not (src / "SKILL.md").exists():
        raise SkillInstallError(f"source path has no SKILL.md: {src}")
    target_root = skills_root / target_source
    target_root.mkdir(parents=True, exist_ok=True)
    # The CANONICAL name is the SKILL.md frontmatter's ``name:`` (not the
    # source dir name) — that's what /skill show <name> and the index key off.
    # If the user's dir is differently named, the install dir follows the
    # manifest so dir name and manifest stay in sync. Falls back to src.name
    # only when frontmatter is missing/unparseable (loader will reject it
    # anyway).
    canonical_name = _read_manifest_name(src) or src.name
    final_name = resolve_install_name(target_root, canonical_name)
    target = target_root / final_name
    # 3. STEP — copy + sync manifest name if conflict-renamed
    shutil.copytree(src, target)
    if final_name != canonical_name:
        rewrite_skill_md_name(target, final_name)
    # 4. EXIT
    log.skills.info(
        "[skills] install_local: exit",
        extra={"_fields": {"final_name": final_name, "target": str(target)}},
    )
    return InstallResult(name=final_name, path=target)


async def install_from_git_url(
    url: str, skills_root: Path, *, target_source: SkillSource = "installed",
) -> InstallResult:
    """``git clone --depth=1`` ``url`` into ``<skills_root>/<target_source>/<name>/``.

    The skill name is derived from the URL's last path segment minus ``.git``.
    """
    # 1. ENTRY
    log.skills.info(
        "[skills] install_git: entry",
        extra={"_fields": {"url": url, "target_source": target_source}},
    )
    if not (url.startswith("git@") or url.startswith("http://")
            or url.startswith("https://")):
        raise SkillInstallError(f"unsupported URL scheme: {url}")
    derived_name = url.rstrip("/").rsplit("/", 1)[-1]
    if derived_name.endswith(".git"):
        derived_name = derived_name[:-4]
    if not derived_name:
        raise SkillInstallError(f"cannot derive skill name from URL: {url}")
    target_root = skills_root / target_source
    target_root.mkdir(parents=True, exist_ok=True)
    # Use the URL-derived name only for the staging clone; rename later
    # to match the manifest's canonical name (post-clone).
    final_name = resolve_install_name(target_root, derived_name)
    target = target_root / final_name
    # 3. STEP — git clone (depth=1 for speed and to discard history)
    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                subprocess.run,
                ["git", "clone", "--depth=1", url, str(target)],
                check=True, capture_output=True,
            ),
            timeout=_GIT_CLONE_TIMEOUT_S,
        )
    except FileNotFoundError as exc:  # B5
        raise SkillInstallError("git is not installed or not on PATH") from exc
    except TimeoutError as exc:  # B5
        # Clean up partial clone.
        shutil.rmtree(target, ignore_errors=True)
        raise SkillInstallError(
            f"git clone timed out after {_GIT_CLONE_TIMEOUT_S:.0f}s",
        ) from exc
    except subprocess.CalledProcessError as exc:  # B5
        shutil.rmtree(target, ignore_errors=True)
        stderr_tail = (exc.stderr or b"").decode("utf-8", errors="replace")[-200:]
        raise SkillInstallError(f"git clone failed: {stderr_tail}") from exc
    # Strip .git directory so the install is a clean copy (and not a checkout).
    shutil.rmtree(target / ".git", ignore_errors=True)
    if not (target / "SKILL.md").exists():
        shutil.rmtree(target, ignore_errors=True)
        raise SkillInstallError(
            f"cloned repository has no SKILL.md at the root: {url}",
        )
    # After clone, prefer the manifest's canonical name. If the manifest says
    # something different from what we cloned into, move the dir.
    manifest_name = _read_manifest_name(target)
    if manifest_name and manifest_name != final_name:
        renamed = resolve_install_name(target_root, manifest_name)
        new_target = target_root / renamed
        shutil.move(str(target), str(new_target))
        target = new_target
        final_name = renamed
        if final_name != manifest_name:
            rewrite_skill_md_name(target, final_name)
    # 4. EXIT
    log.skills.info(
        "[skills] install_git: exit",
        extra={"_fields": {"final_name": final_name, "target": str(target)}},
    )
    return InstallResult(name=final_name, path=target)


async def install_from_archive_url(
    url: str, skills_root: Path, *, target_source: SkillSource = "installed",
) -> InstallResult:
    """Download a ``.tar``/``.tar.gz``/``.zip`` and extract under ``installed/``.

    Defends against:
    * Oversized downloads (``_MAX_ARCHIVE_BYTES``)
    * Zip-bomb expansion (``_MAX_EXTRACTED_BYTES``)
    * Path traversal (rejects entries with absolute paths or ``..``)
    """
    # 1. ENTRY
    log.skills.info(
        "[skills] install_archive: entry",
        extra={"_fields": {"url": url, "target_source": target_source}},
    )
    # 3. STEP — download with cap
    try:
        async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT_S, follow_redirects=True) as c:
            resp = await c.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:  # B5
        raise SkillInstallError(f"download failed: {exc}") from exc
    data = resp.content
    if len(data) > _MAX_ARCHIVE_BYTES:
        raise SkillInstallError(
            f"archive too large: {len(data)} bytes > {_MAX_ARCHIVE_BYTES}",
        )
    # 2. DECISION — pick extractor by content (NOT just suffix — a redirect can rewrite)
    is_zip = data[:4] == b"PK\x03\x04"
    is_gz = data[:2] == b"\x1f\x8b"
    is_tar_plain = len(data) > 262 and data[257:262] == b"ustar"
    if not (is_zip or is_gz or is_tar_plain):
        raise SkillInstallError(
            "downloaded file is not a recognized archive (zip/tar/tar.gz)",
        )
    # 3. STEP — extract to staging dir then move
    with tempfile.TemporaryDirectory(prefix="stackowl-skill-") as staging_str:
        staging = Path(staging_str)
        if is_zip:
            _safe_extract_zip(data, staging)
        else:
            _safe_extract_tar(data, staging, gzipped=is_gz)
        # Locate SKILL.md — accept either at the root OR one subdir deep
        # (most GitHub tarballs nest under <repo>-<sha>/).
        skill_root = _locate_skill_root(staging)
        if skill_root is None:
            raise SkillInstallError("extracted archive has no SKILL.md")
        # Prefer the manifest's canonical name; fall back to extracted dir name.
        manifest_name = _read_manifest_name(skill_root)
        canonical_name = manifest_name or skill_root.name
        target_root = skills_root / target_source
        target_root.mkdir(parents=True, exist_ok=True)
        final_name = resolve_install_name(target_root, canonical_name)
        target = target_root / final_name
        shutil.copytree(skill_root, target)
        if final_name != canonical_name:
            rewrite_skill_md_name(target, final_name)
    # 4. EXIT
    log.skills.info(
        "[skills] install_archive: exit",
        extra={"_fields": {"final_name": final_name, "target": str(target)}},
    )
    return InstallResult(name=final_name, path=target)


def _safe_extract_zip(data: bytes, dest: Path) -> None:
    """Extract a zip into ``dest`` with path-traversal + size-bomb guards."""
    extracted = 0
    with zipfile.ZipFile(BytesIO(data)) as zf:
        for info in zf.infolist():
            name = info.filename
            if name.startswith("/") or ".." in Path(name).parts:
                raise SkillInstallError(
                    f"zip entry path-traversal attempt: {name}",
                )
            extracted += info.file_size
            if extracted > _MAX_EXTRACTED_BYTES:
                raise SkillInstallError(
                    f"zip extraction exceeds size ceiling ({_MAX_EXTRACTED_BYTES} bytes)",
                )
        zf.extractall(dest)


def _open_tar_gz(data: bytes) -> tarfile.TarFile:
    return tarfile.open(fileobj=BytesIO(data), mode="r:gz")


def _open_tar_plain(data: bytes) -> tarfile.TarFile:
    return tarfile.open(fileobj=BytesIO(data), mode="r:")


def _safe_extract_tar(data: bytes, dest: Path, *, gzipped: bool) -> None:
    """Extract a tar into ``dest`` with path-traversal + size-bomb guards."""
    extracted = 0
    _open_tar = _open_tar_gz if gzipped else _open_tar_plain
    with _open_tar(data) as tf:
        for member in tf.getmembers():
            name = member.name
            if name.startswith("/") or ".." in Path(name).parts:
                raise SkillInstallError(
                    f"tar entry path-traversal attempt: {name}",
                )
            extracted += member.size
            if extracted > _MAX_EXTRACTED_BYTES:
                raise SkillInstallError(
                    f"tar extraction exceeds size ceiling ({_MAX_EXTRACTED_BYTES} bytes)",
                )
        # Python 3.12+: filter='data' is the recommended safe extraction.
        tf.extractall(dest, filter="data")


def _locate_skill_root(staging: Path) -> Path | None:
    """Return the dir containing SKILL.md — root or one subdir deep."""
    if (staging / "SKILL.md").exists():
        return staging
    # GitHub-style archives nest under <repo>-<sha>/.
    for child in staging.iterdir():
        if child.is_dir() and (child / "SKILL.md").exists():
            return child
    return None


async def reindex_after_change(
    loader: SkillLoader,
    store: SkillIndexStore,
    skills_root: Path,
    *,
    embedding_registry: EmbeddingRegistry | None = None,
) -> list[LoadedSkill]:
    """Convenience: rescan ``skills_root`` so the SQLite index reflects disk.

    Called after install / remove / enable / disable / reload so the next
    ``/skill list`` (and semantic retrieval) is accurate without waiting for
    the next boot. When ``embedding_registry`` is passed, the assembly's
    embed pass runs for newly added skills — without this, ``/skill add``
    skills never get embeddings and are invisible to ``_gather_relevant_skills``.
    """
    log.skills.debug(
        "[skills] reindex_after_change: entry",
        extra={"_fields": {
            "skills_root": str(skills_root),
            "has_embedding": embedding_registry is not None,
        }},
    )
    loaded = await loader.load_all(skills_root, store=store)
    if embedding_registry is not None:
        # Deferred import to avoid the skills→commands→skills cycle at module load.
        from stackowl.skills.assembly import _embed_missing

        try:
            await _embed_missing(loaded, store, embedding_registry)
        except Exception as exc:  # B5
            log.skills.warning(
                "[skills] reindex_after_change: embed pass failed — retrieval may be stale",
                exc_info=exc,
            )
    log.skills.debug(
        "[skills] reindex_after_change: exit",
        extra={"_fields": {"loaded": len(loaded)}},
    )
    return loaded

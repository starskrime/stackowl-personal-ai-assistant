"""ProfileBackupHandler — weekly tar of browser profile dirs.

Browser profiles accumulate logged-in state (cookies, localStorage). When a
Camoufox version bump invalidates a profile or the user accidentally deletes
the dir, a recent backup lets us restore the login state.
"""

from __future__ import annotations

import contextlib
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult

_DEFAULT_RETENTION = 4


class ProfileBackupHandler(JobHandler):
    """Tars every persistent profile dir and prunes old archives.

    Optional job ``params``: ``{"retention": 4}`` — how many most-recent
    archives per profile to keep.
    """

    def __init__(self, profiles_dir: Path, backups_dir: Path) -> None:
        self._profiles_dir = profiles_dir
        self._backups_dir = backups_dir
        self._backups_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            self._backups_dir.chmod(0o700)

    @property
    def handler_name(self) -> str:
        return "profile_backup"

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        retention = int(job.params.get("retention", _DEFAULT_RETENTION))
        log.scheduler.info(
            "[scheduler] profile_backup.execute: entry",
            extra={"_fields": {"job_id": job.job_id, "retention": retention}},
        )
        if not self._profiles_dir.exists():
            log.scheduler.info(
                "[scheduler] profile_backup: no profiles dir yet — nothing to back up",
                extra={"_fields": {"path": str(self._profiles_dir)}},
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change", success=True, output="no profiles",
                error=None, duration_ms=(time.monotonic() - t0) * 1000,
            )

        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        archives_created: list[str] = []
        errors: list[str] = []

        # Profiles are nested: <profiles_dir>/<owner_key>/<profile_name>/
        for owner_dir in sorted(self._profiles_dir.iterdir()):
            if not owner_dir.is_dir():
                continue
            for profile_dir in sorted(owner_dir.iterdir()):
                if not profile_dir.is_dir():
                    continue
                archive_name = f"{owner_dir.name}__{profile_dir.name}__{ts}.tar.gz"
                archive_path = self._backups_dir / archive_name
                try:
                    with tarfile.open(archive_path, "w:gz") as tar:
                        tar.add(profile_dir, arcname=profile_dir.name)
                    with contextlib.suppress(OSError):
                        archive_path.chmod(0o600)
                    archives_created.append(str(archive_path))
                except (OSError, tarfile.TarError) as exc:
                    errors.append(f"{profile_dir}: {exc}")
                    log.scheduler.warning(
                        "[scheduler] profile_backup: tar failed",
                        exc_info=exc,
                        extra={"_fields": {"profile": str(profile_dir)}},
                    )

        pruned = self._prune_retention(retention)
        duration_ms = (time.monotonic() - t0) * 1000
        log.scheduler.info(
            "[scheduler] profile_backup.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id,
                "created": len(archives_created),
                "pruned": pruned,
                "errors": len(errors),
                "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change",
            success=len(errors) == 0,
            output=f"created={len(archives_created)} pruned={pruned}",
            error="; ".join(errors) if errors else None,
            duration_ms=duration_ms,
            metadata={"created": len(archives_created), "pruned": pruned},
        )

    def _prune_retention(self, retention: int) -> int:
        """Keep the N most-recent archives per (owner, profile); delete older."""
        if retention < 1:
            return 0
        # Group by "<owner>__<profile>__" prefix.
        by_profile: dict[str, list[Path]] = {}
        for archive in self._backups_dir.glob("*.tar.gz"):
            parts = archive.stem.split("__")
            if len(parts) < 3:
                continue
            key = f"{parts[0]}__{parts[1]}"
            by_profile.setdefault(key, []).append(archive)

        pruned = 0
        for archives in by_profile.values():
            archives.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for old in archives[retention:]:
                try:
                    old.unlink()
                    pruned += 1
                except OSError as exc:
                    log.scheduler.warning(
                        "[scheduler] profile_backup: prune unlink failed",
                        exc_info=exc,
                        extra={"_fields": {"path": str(old)}},
                    )
        return pruned


def register_profile_backup_handler(profiles_dir: Path, backups_dir: Path) -> None:
    handler = ProfileBackupHandler(profiles_dir=profiles_dir, backups_dir=backups_dir)
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] profile_backup handler registered",
        extra={"_fields": {"handler": handler.handler_name}},
    )

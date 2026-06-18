"""StackowlHome — single source of truth for all ~/.stackowl/ paths."""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["StackowlHome"]


class StackowlHome:
    """All persistent paths derive from a single home root.

    Env var precedence (high → low):
    1. Per-path legacy vars (STACKOWL_CONFIG_FILE, STACKOWL_DATA_DIR,
       STACKOWL_LOG_DIR, STACKOWL_PID_FILE) — kept for CI back-compat.
    2. STACKOWL_HOME — overrides the root; all sub-paths derive from it.
    3. Default: Path.home() / ".stackowl"

    Nothing is ever written inside the project directory at runtime.
    """

    @classmethod
    def home(cls) -> Path:
        raw = os.environ.get("STACKOWL_HOME")
        return Path(raw) if raw else Path.home() / ".stackowl"

    @classmethod
    def config_file(cls) -> Path:
        raw = os.environ.get("STACKOWL_CONFIG_FILE")
        return Path(raw) if raw else cls.home() / "stackowl.yaml"

    @classmethod
    def secrets_dir(cls) -> Path:
        return cls.home() / ".secrets"

    @classmethod
    def workspace(cls) -> Path:
        raw = os.environ.get("STACKOWL_DATA_DIR")
        return Path(raw) if raw else cls.home() / "workspace"

    @classmethod
    def db_path(cls) -> Path:
        return cls.workspace() / "stackowl.db"

    @classmethod
    def kuzu_dir(cls) -> Path:
        return cls.workspace() / "kuzu"

    @classmethod
    def lancedb_dir(cls) -> Path:
        return cls.workspace() / "lancedb"

    @classmethod
    def tools_dir(cls) -> Path:
        return cls.workspace() / "tools"

    @classmethod
    def learned_tools_dir(cls) -> Path:
        """Agent-authored tool specs (H4 tool_build).

        Each ``*.json`` is one declarative LearnedToolSpec the agent minted; the
        boot loader reads them back into the registry on every start so a learned
        tool survives reboots. Lives under ``tools/learned`` (distinct from the
        ``tools`` root, which is reserved for other tool-state).
        """
        return cls.tools_dir() / "learned"

    @classmethod
    def knowledge_dir(cls) -> Path:
        return cls.workspace() / "knowledge"

    @classmethod
    def skills_dir(cls) -> Path:
        """Workspace root for the unified Skills concept (Learning Commit 3).

        Subdirs: builtin/ (shipped, read-only-by-agent), installed/, user/,
        learned/. Files in here are the source of truth; the ``skills`` index
        in SQLite is a cache.
        """
        return cls.workspace() / "skills"

    @classmethod
    def logs_dir(cls) -> Path:
        raw = os.environ.get("STACKOWL_LOG_DIR")
        return Path(raw) if raw else cls.home() / "logs"

    @classmethod
    def audit_sink_failures_file(cls) -> Path:
        """Durable, append-only marker sink for failed audit writes (SEC-7 / F137).

        SEPARATE from the tamper-evident audit_log DB on purpose: when that DB
        write fails, a security event that should have been audited is otherwise
        lost to a single ERROR log line. A JSONL marker here records (durably,
        under ``~/.stackowl``) that an audited security event was dropped, so the
        operator can reconstruct it. Never contains a secret value.
        """
        return cls.logs_dir() / "audit_sink_failures.jsonl"

    @classmethod
    def plugins_dir(cls) -> Path:
        return cls.home() / "plugins"

    @classmethod
    def providers_dir(cls) -> Path:
        """User-provided provider overrides (~/.stackowl/providers/*.yaml)."""
        return cls.home() / "providers"

    @classmethod
    def pid_file(cls) -> Path:
        raw = os.environ.get("STACKOWL_PID_FILE")
        return Path(raw) if raw else cls.home() / "runtime" / "stackowl.pid"

    @classmethod
    def screenshots_dir(cls) -> Path:
        return cls.home() / "screenshots"

    @classmethod
    def browser_profiles_dir(cls) -> Path:
        return cls.home() / "browser-profiles"

    @classmethod
    def downloads_dir(cls) -> Path:
        """The single canonical downloads folder.

        Lives UNDER the workspace (not the home root) so ``send_file`` can deliver
        from it, yet is a sibling of — not mixed in with — the persistent stores
        (stackowl.db / lancedb / kuzu / skills / knowledge) that live at the
        workspace ROOT. That separation lets the downloads janitor prune this
        folder on a schedule without ever touching durable state.
        """
        return cls.workspace() / "downloads"

    @classmethod
    def browser_cache_dir(cls) -> Path:
        return cls.home() / "cache" / "browser"

    @classmethod
    def models_dir(cls) -> Path:
        """Downloaded model weights (TTS voices, local image/vision models).

        Lives at the home ROOT (durable, never pruned) — weights are expensive to
        re-download, not user deliverables. The agent auto-installs heavy weights
        here ([[feedback_agent_auto_install]]); media tools read them lazily.
        """
        return cls.home() / "models"

    @classmethod
    def media_dir(cls) -> Path:
        """Generated media artifacts (synthesized audio, generated images).

        Lives UNDER the workspace (like ``downloads_dir``) so ``send_file`` can
        deliver from it and the janitor can prune it on a schedule, kept apart from
        the durable stores at the workspace root.
        """
        return cls.workspace() / "media"

    @classmethod
    def ensure_exists(cls) -> None:
        """Create the full home tree. Idempotent."""
        for d in (
            cls.home(),
            cls.secrets_dir(),
            cls.workspace(),
            cls.kuzu_dir(),
            cls.lancedb_dir(),
            cls.tools_dir(),
            cls.learned_tools_dir(),
            cls.knowledge_dir(),
            cls.skills_dir(),
            cls.skills_dir() / "builtin",
            cls.skills_dir() / "installed",
            cls.skills_dir() / "user",
            cls.skills_dir() / "learned",
            cls.logs_dir(),
            cls.plugins_dir(),
            cls.providers_dir(),
            cls.pid_file().parent,
            cls.screenshots_dir(),
            cls.browser_profiles_dir(),
            cls.downloads_dir(),
            cls.browser_cache_dir(),
            cls.models_dir(),
            cls.media_dir(),
        ):
            d.mkdir(parents=True, exist_ok=True)
        import contextlib
        with contextlib.suppress(OSError):
            cls.secrets_dir().chmod(0o700)
            cls.browser_profiles_dir().chmod(0o700)
        # Both the legacy and the new downloads dir now exist (the mkdir loop
        # created the new one); migrate any files left in the legacy location.
        cls.migrate_legacy_downloads()

    @classmethod
    def migrate_legacy_downloads(cls) -> None:
        """Move any files from the legacy ``~/.stackowl/downloads`` into the new
        workspace downloads dir. Idempotent, best-effort, NEVER raises.

        The downloads folder was relocated from the home root to under the
        workspace. This one-shot, self-healing migration moves any leftover
        entries from the old location so a user upgrading in place keeps their
        files. It is a no-op when the legacy dir is absent, empty, or already the
        same resolved path as the new dir.
        """
        from stackowl.infra.observability import log

        try:
            import contextlib
            import shutil

            legacy = cls.home() / "downloads"
            target = cls.downloads_dir()
            if not legacy.is_dir():
                return
            try:
                same = legacy.resolve() == target.resolve()
            except OSError:
                same = False
            if same:
                return
            entries = list(legacy.iterdir())
            if not entries:
                # Empty legacy dir — clean it up and bail.
                with contextlib.suppress(OSError):
                    legacy.rmdir()
                return

            target.mkdir(parents=True, exist_ok=True)
            moved = 0
            for entry in entries:
                dest = target / entry.name
                if dest.exists():
                    log.startup.warning(
                        "[paths] migrate_legacy_downloads: name clash — skipping",
                        extra={"_fields": {"entry": entry.name}},
                    )
                    continue
                try:
                    shutil.move(str(entry), str(dest))
                    moved += 1
                except OSError as exc:
                    log.startup.warning(
                        "[paths] migrate_legacy_downloads: move failed — skipping",
                        exc_info=exc,
                        extra={"_fields": {"entry": entry.name}},
                    )
            # Remove the now-(hopefully)-empty legacy dir; suppress if anything
            # was left behind (a skipped clash/error).
            with contextlib.suppress(OSError):
                legacy.rmdir()
            log.startup.info(
                "[paths] migrate_legacy_downloads: migrated legacy downloads",
                extra={"_fields": {"moved": moved, "target": str(target)}},
            )
        except Exception as exc:  # never let a migration crash startup
            log.startup.error(
                "[paths] migrate_legacy_downloads: unexpected failure — skipped",
                exc_info=exc,
            )

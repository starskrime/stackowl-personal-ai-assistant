"""StackowlHome — single source of truth for all ~/.stackowl/ paths."""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["StackowlHome", "migrate_legacy_skills", "skills_dir_is_outside_workspace"]


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
        """The knowledge-graph directory.

        ``home()/kuzu``, NOT ``workspace()/kuzu``. This accessor used to return
        the latter while ``MemoryAssembly.build`` opened the former directly,
        so there were two graph locations and only one of them had ever been
        written. Measured 2026-08-05:

            ~/.stackowl/kuzu            30M, WAL modified that day   <- live
            ~/.stackowl/workspace/kuzu  empty, created 2026-05-24

        Unified onto the live path rather than the "tidier" one: the alternative
        was migrating 30MB of the user's graph to satisfy a naming preference,
        which is all risk and no benefit. Nothing had pinned this location — no
        test asserted it and the only consumer passed its own path — which is
        exactly why the split survived unnoticed.
        """
        return cls.home() / "kuzu"

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
    def worktrees_dir(cls) -> Path:
        """Scratch git worktrees for isolated coding runs (claude_code, epic orchestration).

        Never inside the target repo itself — kept under our own workspace so a
        throwaway branch/checkout is never mistaken for tracked project state.
        """
        return cls.workspace() / "worktrees"

    @classmethod
    def knowledge_dir(cls) -> Path:
        return cls.workspace() / "knowledge"

    @classmethod
    def skills_dir(cls) -> Path:
        """Root for the unified Skills concept (Learning Commit 3).

        Subdirs: builtin/ (shipped, read-only-by-agent), installed/, user/,
        learned/. Files in here are the source of truth; the ``skills`` index
        in SQLite is a cache.

        D05.1 — DELIBERATELY A SIBLING OF ``workspace()``, NOT A CHILD OF IT.
        This used to be ``workspace() / "skills"``, which put an EXECUTABLE tree
        inside the one ``write_file`` is confined to: ``SkillLoader`` calls
        ``exec_module()`` on every ``skills/**/tools/*.py`` at boot, so any tool
        able to write the workspace could get arbitrary Python executed at the
        next start. ``write_file`` is a base, always-presented tool, and after
        D05.5 sandboxed PTC code can call it too — making it a sandbox escape
        with a one-reboot delay.

        Moved rather than fenced with a "no .py writes under skills/" rule,
        because such a rule has to be re-applied for every future executable
        tree, which is exactly how the hole appeared. Nothing about skill
        INSTALLATION depended on the location: every writer resolves through this
        method and writes directly, never through write_file's guard.

        Verified by :func:`skills_dir_is_outside_workspace`, which the startup
        path asserts — see its docstring for the STACKOWL_DATA_DIR edge case.
        """
        return cls.home() / "skills"

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
    def core_socket(cls) -> Path:
        """The gateway<->core unix-domain socket for the two-process split.

        Lives in the runtime dir next to the PID file. Overridable via
        ``runtime.socket_path`` in config (resolved by the orchestrator); this is
        the default when that override is unset.
        """
        return cls.home() / "runtime" / "core.sock"

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
        (stackowl.db / lancedb / kuzu / knowledge) that live at the workspace
        ROOT. (``skills`` was in that list until D05.1 moved it OUT of the
        workspace entirely — see :meth:`skills_dir`.) That separation lets the downloads janitor prune this
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
        # D05.1 — relocate skills out of the model-writable workspace. Runs AFTER
        # the mkdir loop so the target tree already exists, and beside the
        # downloads migration because this is the one place every process calls
        # on the way up.
        migrate_legacy_skills()

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


def skills_dir_is_outside_workspace() -> bool:
    """Whether the skills tree sits OUTSIDE the model-writable workspace (D05.1).

    The security property this item exists to establish, expressed as something
    checkable rather than as a comment. ``write_file`` confines to
    ``workspace()``; ``SkillLoader`` executes ``skills/**/tools/*.py`` at boot. If
    those two trees ever overlap again, the model can write code that runs at the
    next start.

    NOT redundant with reading ``skills_dir()``: the two are independent under
    ``STACKOWL_DATA_DIR``, which relocates ``workspace()`` but not ``home()``. In
    the normal case they are siblings under ``~/.stackowl``; a pathological
    ``STACKOWL_DATA_DIR=~/.stackowl`` would make workspace the PARENT of skills
    and silently restore the hole. Hence a runtime check, not an assumption.
    """
    try:
        skills = StackowlHome.skills_dir().resolve()
        workspace = StackowlHome.workspace().resolve()
    except OSError:
        return False  # cannot prove it is safe → report unsafe
    return workspace not in skills.parents and skills != workspace


def migrate_legacy_skills() -> None:
    """Relocate ``workspace/skills`` → ``skills`` (D05.1). Idempotent. Never raises.

    COPY, VERIFY, THEN REMOVE — deliberately not ``shutil.move``. An interrupted
    move would leave skills split across two trees with no record of which half
    landed. A crash mid-copy leaves the ORIGINAL intact and the migration re-runs.

    THIS FUNCTION DESTROYED 419 SKILLS ON ITS FIRST RUN. The bug is worth stating
    because the shape of it is generic and the fix is not obvious from the outside:

      ``ensure_exists()`` mkdirs ``skills/{builtin,installed,user,learned}``
      BEFORE calling this. The first version skipped any entry whose destination
      already existed — treating those four freshly-created EMPTY directories as
      "already migrated" — and then its verify only asked whether each name
      existed at the target. Four empty dirs existed, so verify passed, and
      ``rmtree`` deleted the source.

    Two corrections, both load-bearing:
      1. RECURSE into a destination directory that already exists instead of
         skipping it. An existing directory means "partially migrated", never
         "done".
      2. VERIFY BY COUNTING FILES, not by testing that a name exists. A name
         proves nothing; the original failure passed a name check with an empty
         directory. Nothing is deleted unless the target holds at least as many
         files as the source.
    """
    import contextlib
    import shutil

    from stackowl.infra.observability import log

    try:
        legacy = StackowlHome.workspace() / "skills"
        target = StackowlHome.skills_dir()
        if not legacy.is_dir():
            return
        try:
            if legacy.resolve() == target.resolve():
                return  # already the same tree (e.g. an odd STACKOWL_DATA_DIR)
        except OSError:
            return

        entries = list(legacy.iterdir())
        log.startup.info(
            "[paths] migrate_legacy_skills: relocating skills out of the "
            "model-writable workspace (D05.1)",
            extra={"_fields": {
                "from": str(legacy), "to": str(target), "entries": len(entries),
            }},
        )
        if not entries:
            with contextlib.suppress(OSError):
                legacy.rmdir()
            return

        target.mkdir(parents=True, exist_ok=True)

        def _n_files(root: Path) -> int:
            """Count files under a tree. The unit of verification — see below."""
            return sum(1 for p in root.rglob("*") if p.is_file())

        source_files = _n_files(legacy)
        copied = 0
        for entry in entries:
            dest = target / entry.name
            if entry.is_dir():
                # dirs_exist_ok: ensure_exists() has ALREADY created the four
                # standard subdirs, so a pre-existing destination is the normal
                # case and means PARTIALLY migrated, never "done". Skipping it
                # here is what destroyed 419 skills on the first run.
                shutil.copytree(entry, dest, dirs_exist_ok=True)
            elif not dest.exists():
                shutil.copy2(entry, dest)
            copied += 1

        # VERIFY BY FILE COUNT, not by name existence. The original check asked
        # "does a path with this name exist at the target?" — which four empty
        # directories satisfied, so it passed and the source was deleted.
        target_files = _n_files(target)
        if target_files < source_files:
            log.startup.error(
                "[paths] migrate_legacy_skills: verify FAILED — target holds "
                "fewer files than the source; KEEPING the legacy tree",
                extra={"_fields": {
                    "source_files": source_files, "target_files": target_files,
                }},
            )
            return

        shutil.rmtree(legacy, ignore_errors=True)
        log.startup.info(
            "[paths] migrate_legacy_skills: done",
            extra={"_fields": {
                "copied_entries": copied, "source_files": source_files,
                "target_files": target_files,
            }},
        )
    except Exception as exc:  # never let a migration crash startup
        log.startup.error(
            "[paths] migrate_legacy_skills: unexpected failure — skipped",
            exc_info=exc,
        )

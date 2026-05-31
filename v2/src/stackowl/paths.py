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
        return cls.home() / "downloads"

    @classmethod
    def browser_cache_dir(cls) -> Path:
        return cls.home() / "cache" / "browser"

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
        ):
            d.mkdir(parents=True, exist_ok=True)
        import contextlib
        with contextlib.suppress(OSError):
            cls.secrets_dir().chmod(0o700)
            cls.browser_profiles_dir().chmod(0o700)

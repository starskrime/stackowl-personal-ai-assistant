"""ServiceInstaller — detects OS and installs the appropriate native service."""

from __future__ import annotations

import importlib.resources
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer

from stackowl.infra.observability import log

_DEPLOY_SUBDIR = "deploy"


def _find_deploy_dir() -> Path:
    """Locate the ``deploy/`` directory bundled alongside the package.

    Resolution order:
    1. ``importlib.resources`` anchor relative to the ``stackowl`` package.
    2. Walk up from ``__file__`` looking for a ``deploy/`` sibling of ``src/``.
    """
    # 1. Try importlib.resources (works in installed wheels)
    try:
        anchor = importlib.resources.files("stackowl")
        # anchor is inside src/stackowl — go up to src/, then up to project root
        pkg_path = Path(str(anchor))
        for parent in [pkg_path.parent, pkg_path.parent.parent, pkg_path.parent.parent.parent]:
            candidate = parent / _DEPLOY_SUBDIR
            if candidate.is_dir():
                return candidate
    except Exception:
        pass

    # 2. Walk up from __file__
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / _DEPLOY_SUBDIR
        if candidate.is_dir():
            return candidate

    raise FileNotFoundError(
        "Cannot locate 'deploy/' directory. "
        "Run from the project root or install the package properly."
    )


class ServiceInstaller:
    """Installs StackOwl as a native OS service.

    Args:
        user_mode: If True, install as a user-level service (Linux only for now).
    """

    def __init__(self, user_mode: bool = False) -> None:
        self._user_mode = user_mode
        self._deploy_dir: Path | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def install(self) -> None:
        """Detect the current platform and run the appropriate installer."""
        # 1. ENTRY
        log.infra.debug(
            "[installer] install: entry",
            extra={"_fields": {"platform": sys.platform, "user_mode": self._user_mode}},
        )

        # 2. DECISION — resolve deploy directory once
        try:
            self._deploy_dir = _find_deploy_dir()
        except FileNotFoundError as exc:
            log.infra.error("[installer] install: deploy dir not found — %s", exc)
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc

        log.infra.debug(
            "[installer] install: decision — deploy_dir=%s platform=%s",
            self._deploy_dir,
            sys.platform,
        )

        # 3. STEP — dispatch
        if sys.platform == "linux":
            self._install_linux()
        elif sys.platform == "darwin":
            self._install_macos()
        elif sys.platform == "win32":
            self._install_windows()
        else:
            log.infra.error("[installer] install: unsupported platform — %s", sys.platform)
            typer.echo(f"Error: unsupported platform '{sys.platform}'", err=True)
            raise typer.Exit(1)

        # 4. EXIT
        log.infra.info("[installer] install: exit — service installation complete")

    # ------------------------------------------------------------------
    # Platform-specific installers
    # ------------------------------------------------------------------

    def _install_linux(self) -> None:
        """Install the systemd service unit on Linux."""
        assert self._deploy_dir is not None  # guaranteed by install()

        # 1. ENTRY
        log.infra.debug("[installer] _install_linux: entry", extra={"_fields": {"user_mode": self._user_mode}})

        src = self._deploy_dir / "stackowl.service"

        if self._user_mode:
            dest_dir = Path.home() / ".config" / "systemd" / "user"
            reload_args = ["systemctl", "--user", "daemon-reload"]
        else:
            dest_dir = Path("/etc/systemd/system")
            reload_args = ["systemctl", "daemon-reload"]

        dest = dest_dir / "stackowl.service"

        # 2. DECISION
        log.infra.debug(
            "[installer] _install_linux: decision — dest=%s user_mode=%s", dest, self._user_mode
        )

        # 3. STEP — copy unit file
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
            log.infra.info("[installer] _install_linux: step — unit file written to %s", dest)
        except PermissionError as exc:
            log.infra.error("[installer] _install_linux: permission denied writing unit file — %s", exc)
            typer.echo(
                "Error: permission denied. Run with sudo for system-level install, "
                "or use --user for a user-level service.",
                err=True,
            )
            raise typer.Exit(1) from exc

        # daemon-reload
        self._run(reload_args, "systemctl daemon-reload")

        # 4. EXIT
        if self._user_mode:
            next_step = "systemctl --user enable --now stackowl"
        else:
            next_step = "sudo systemctl enable --now stackowl"

        typer.echo(f"✓ StackOwl service installed — run: {next_step}")
        self._audit("linux")

    def _install_macos(self) -> None:
        """Install the launchd plist on macOS."""
        assert self._deploy_dir is not None

        # 1. ENTRY
        log.infra.debug("[installer] _install_macos: entry")

        src = self._deploy_dir / "com.stackowl.plist"
        launch_agents = Path.home() / "Library" / "LaunchAgents"
        dest = launch_agents / "com.stackowl.plist"

        # 2. DECISION
        log.infra.debug("[installer] _install_macos: decision — dest=%s", dest)

        # 3. STEP — copy plist
        try:
            launch_agents.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
            log.infra.info("[installer] _install_macos: step — plist written to %s", dest)
        except OSError as exc:
            log.infra.error("[installer] _install_macos: failed to write plist — %s", exc)
            typer.echo(f"Error writing plist: {exc}", err=True)
            raise typer.Exit(1) from exc

        # launchctl load
        self._run(
            ["launchctl", "load", str(dest)],
            "launchctl load",
        )

        # 4. EXIT
        typer.echo("✓ StackOwl service installed — loaded via launchd")
        self._audit("darwin")

    def _install_windows(self) -> None:
        """Run the PowerShell NSSM installer on Windows."""
        assert self._deploy_dir is not None

        # 1. ENTRY
        log.infra.debug("[installer] _install_windows: entry")

        script = self._deploy_dir / "install-service.ps1"

        # 2. DECISION
        log.infra.debug("[installer] _install_windows: decision — script=%s", script)

        # 3. STEP
        result = subprocess.run(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            capture_output=False,
            check=False,
        )
        log.infra.info(
            "[installer] _install_windows: step — powershell returned %d", result.returncode
        )

        if result.returncode != 0:
            log.infra.error("[installer] _install_windows: PowerShell script failed — rc=%d", result.returncode)
            typer.echo(f"Error: PowerShell script exited with code {result.returncode}", err=True)
            raise typer.Exit(result.returncode)

        # 4. EXIT
        typer.echo("✓ StackOwl service installed via NSSM")
        self._audit("win32")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run(self, cmd: list[str], label: str) -> None:
        """Run *cmd* and log the result. Raises typer.Exit on failure."""
        log.infra.debug("[installer] _run: step — %s", label)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                log.infra.warning(
                    "[installer] _run: %s returned %d — stderr=%s",
                    label,
                    result.returncode,
                    result.stderr.strip(),
                )
            else:
                log.infra.debug("[installer] _run: %s succeeded", label)
        except FileNotFoundError as exc:
            log.infra.warning("[installer] _run: command not found — %s: %s", label, exc)

    def _audit(self, platform: str) -> None:
        """Append a service_installed event to the audit log if available."""
        details: dict[str, Any] = {"platform": platform, "user_mode": self._user_mode}
        try:
            from stackowl.audit.logger import AuditLogger
            from stackowl.db.pool import default_db_path

            AuditLogger(default_db_path()).append(
                event_type="service_installed",
                actor="user",
                target=None,
                details=details,
            )
            log.infra.debug("[installer] _audit: audit event appended")
        except Exception as exc:
            # Audit failure must never block installation
            log.infra.warning("[installer] _audit: could not write audit log — %s", exc)

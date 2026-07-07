"""Dispatch tests — /browser profile delete reports real outcome.

The original code used ``contextlib.suppress(OSError)`` then returned
"Deleted profile '<x>'" unconditionally, claiming success even when
shutil.rmtree failed.  The fix checks the directory is gone (or catches
and reports the OSError) before returning a success message.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_runtime(profiles_dir: Path) -> MagicMock:
    """Return a fake BrowserRuntime whose settings.profiles_dir points at tmp."""
    rt = MagicMock()
    rt.settings.profiles_dir = profiles_dir
    return rt


def _make_fake_services(runtime: MagicMock | None) -> MagicMock:
    """Return a fake ServiceLocator."""
    svc = MagicMock()
    svc.browser_runtime = runtime
    svc.browser_sessions = None
    return svc


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_browser_profile_delete_success(tmp_path: Path) -> None:
    """Deleting an existing profile returns the success message."""
    profiles_dir = tmp_path / "profiles"
    owner_dir = profiles_dir / "local"
    profile_dir = owner_dir / "my_profile"
    profile_dir.mkdir(parents=True)

    runtime = _make_fake_runtime(profiles_dir)
    fake_svc = _make_fake_services(runtime)

    deps = CommandDeps()
    with patch("stackowl.commands.browser_command.get_services", return_value=fake_svc):
        register_all_commands(deps, registry=CommandRegistry.instance())
        result = (
            await CommandRegistry.instance().dispatch(
                "browser", "profile delete my_profile", make_state()
            )
        ).text

    assert "Deleted profile 'my_profile'" in result
    assert not profile_dir.exists(), "Directory must actually be removed"


async def test_browser_profile_delete_rejects_path_traversal(tmp_path: Path) -> None:
    """A '..' profile name must be rejected before touching the filesystem —
    it must NOT rmtree the parent profiles_dir."""
    profiles_dir = tmp_path / "profiles"
    owner_dir = profiles_dir / "local"
    sibling = profiles_dir / "other_owner"
    owner_dir.mkdir(parents=True)
    sibling.mkdir(parents=True)

    runtime = _make_fake_runtime(profiles_dir)
    fake_svc = _make_fake_services(runtime)

    deps = CommandDeps()
    with patch("stackowl.commands.browser_command.get_services", return_value=fake_svc):
        register_all_commands(deps, registry=CommandRegistry.instance())
        result = (
            await CommandRegistry.instance().dispatch(
                "browser", "profile delete ..", make_state()
            )
        ).text

    assert "Invalid profile name" in result
    # Nothing outside the rejected name was touched.
    assert sibling.exists() and profiles_dir.exists()


async def test_browser_profile_delete_not_found(tmp_path: Path) -> None:
    """Deleting a non-existent profile returns honest not-found (no false success)."""
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir(parents=True)

    runtime = _make_fake_runtime(profiles_dir)
    fake_svc = _make_fake_services(runtime)

    deps = CommandDeps()
    with patch("stackowl.commands.browser_command.get_services", return_value=fake_svc):
        register_all_commands(deps, registry=CommandRegistry.instance())
        result = (
            await CommandRegistry.instance().dispatch(
                "browser", "profile delete ghost_profile", make_state()
            )
        ).text

    # Must NOT claim success
    assert "Deleted" not in result
    assert "not found" in result.lower()


async def test_browser_profile_delete_rmtree_failure_honest(tmp_path: Path) -> None:
    """When shutil.rmtree raises OSError the command reports failure — NOT false success."""
    profiles_dir = tmp_path / "profiles"
    owner_dir = profiles_dir / "local"
    profile_dir = owner_dir / "locked_profile"
    profile_dir.mkdir(parents=True)

    runtime = _make_fake_runtime(profiles_dir)
    fake_svc = _make_fake_services(runtime)

    deps = CommandDeps()
    with (
        patch("stackowl.commands.browser_command.get_services", return_value=fake_svc),
        patch(
            "stackowl.commands.browser_command.shutil.rmtree",
            side_effect=OSError("Permission denied"),
        ),
    ):
        register_all_commands(deps, registry=CommandRegistry.instance())
        result = (
            await CommandRegistry.instance().dispatch(
                "browser", "profile delete locked_profile", make_state()
            )
        ).text

    # Must NOT claim "Deleted" — must be an honest failure message
    assert "Deleted" not in result
    assert "✗" in result or "Failed" in result or "could not" in result.lower()


def test_browser_watch_list_has_no_dead_agent_reference() -> None:
    """The /browser watch list help text must not reference the retired /agent command."""
    from stackowl.commands.browser_command import BrowserCommand

    cmd = BrowserCommand()
    result = cmd._watch_subcmd(["list"])
    assert "/agent" not in result

"""Removing software is destruction, and the gate could not see it.

Bakir, 2026-09-10: *"Only dangerous commnds should be approved liked removing os
or platform components, or something dangerous not simple task should be asked
for approval."*

WHY IT WAS INVISIBLE RATHER THAN MERELY MISSING. Every branch of the catastrophic
detector matched a command that destroys BYTES AT A PATH — ``rm`` a system root,
``dd``/``mkfs``/``wipefs``/``shred`` a device, recursive ``chmod``/``chown`` a
system root — and each is found by reading the operands and asking "is this a
protected path". ``apt-get purge systemd`` names no path at all; the package
manager resolves the file list itself. So widening the protected-root set could
never have reached this class however far it was widened, and MEASURED before the
fix, every one of these ran silently: ``apt purge -y systemd``,
``pip uninstall -y stackowl``, ``systemctl disable --now stackowl``,
``dpkg --purge libc6``, ``snap remove core``.

WHAT IT COSTS HIM, measured rather than assumed. Of the 294 commands this tool
actually ran across the 13 retained logs, ZERO are removals — the only two
package/service-manager invocations in the whole corpus are ``systemctl
--state=failed`` and ``systemctl --failed``, both status queries. The new class
fires on none of them, so this adds no prompt to the observed workload. That is
what makes it the other half of his sentence rather than a trade against it.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest

from stackowl.tools.system.shell import ShellTool, is_catastrophic

# --------------------------------------------------------------------------- #
# 1. The class he named, in every dialect the box could reach it through
# --------------------------------------------------------------------------- #

REMOVALS = [
    "apt-get remove --purge python3",
    "apt purge -y systemd",
    "sudo apt-get autoremove --purge",
    "aptitude purge openssh-server",
    "dpkg --purge libc6",
    "dpkg -r libc6",
    "pacman -Rns base",
    "yum remove -y kernel",
    "dnf erase glibc",
    "zypper remove glibc",
    "apk del busybox",
    "snap remove core",
    "flatpak uninstall org.gnome.Calculator",
    "brew uninstall python",
    "pip uninstall -y stackowl",
    "pip3 uninstall stackowl",
    "pipx uninstall stackowl",
    "uv pip uninstall stackowl",
    "python3 -m pip uninstall -y stackowl",
    "conda remove numpy",
    "poetry remove requests",
    "npm uninstall -g typescript",
    "npm un -g typescript",
    "pnpm remove left-pad",
    "yarn remove left-pad",
    "gem uninstall rails",
    "cargo uninstall ripgrep",
    # The aliases each manager documents for the same act.
    "brew rm python",
    "npm rm left-pad",
    "zypper rm glibc",
]

SERVICE_DISABLEMENTS = [
    "systemctl disable --now stackowl",
    "systemctl mask ssh",
    "sudo systemctl disable docker",
    "service stackowl disable",
    "chkconfig sshd disable",
    "launchctl unload com.example.agent",
]


@pytest.mark.tripwire
@pytest.mark.parametrize("command", REMOVALS + SERVICE_DISABLEMENTS)
def test_removing_software_needs_his_approval(command: str) -> None:
    hit, reason = is_catastrophic(shlex.split(command))
    assert hit, f"{command!r} still runs silently"
    assert reason, "a catastrophic verdict must carry a human reason"


@pytest.mark.tripwire
def test_a_wrapper_does_not_hide_it() -> None:
    """``sudo`` changes WHO runs a command, never WHAT it does — and the base word
    of the dangerous form is ``sudo``, so peeling wrappers is load-bearing here
    rather than a nicety."""
    for command in (
        "sudo apt purge -y openssh-server",
        "env DEBIAN_FRONTEND=noninteractive apt-get remove -y python3",
        "nohup pip uninstall -y stackowl",
        "cd /tmp && sudo apt purge -y openssh-server",
    ):
        hit, _ = is_catastrophic(shlex.split(command))
        assert hit, f"{command!r} evaded the gate through a wrapper"


# --------------------------------------------------------------------------- #
# 2. The expensive direction — and it is the one that decides whether this ships
# --------------------------------------------------------------------------- #

ORDINARY = [
    # The only two package/service-manager commands in the entire live corpus.
    "systemctl --state=failed",
    "systemctl --failed --no-legend --lines=0",
    "service --status-all",
    # Reversible service control. `stop` returns on the next boot; `disable` does
    # not. The platform's own restart path stops things.
    "systemctl stop docker",
    "systemctl restart nginx",
    "systemctl status stackowl",
    # Installation and query, which the tool's description promises run silently.
    "apt list --installed",
    "apt-get update",
    "pip install requests",
    "pip list",
    "uv pip install ruff",
    "uv run pytest",
    "npm install",
    "brew list",
    "conda list",
    "cargo build",
    "go build ./...",
    # Ordinary work.
    "rm -rf ./build",
    "ls -la",
    "git rm oldfile",
    "docker ps",
]


@pytest.mark.tripwire
@pytest.mark.parametrize("command", ORDINARY)
def test_ordinary_work_is_still_silent(command: str) -> None:
    hit, reason = is_catastrophic(shlex.split(command))
    assert not hit, f"{command!r} would now interrupt him: {reason}"


@pytest.mark.tripwire
def test_the_live_corpus_gains_no_prompts() -> None:
    """The claim this fix is sold on, checked against the real record.

    Reads the retained logs rather than a fixture, because the number that
    matters is how many of HIS commands would now stop and ask. A fixture could
    only restate the author's belief about what he runs.

    Skips when the logs have rotated past the corpus — a check whose evidence has
    aged out must say so, never read as a pass.
    """
    log_dir = Path.home() / ".stackowl" / "logs"
    commands: list[str] = []
    for path in sorted(log_dir.glob("stackowl*.jsonl")):
        for line in path.read_text(errors="replace").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("msg") != "shell.execute: entry":
                continue
            command = (record.get("fields") or {}).get("command")
            if command:
                commands.append(command)

    if len(commands) < 50:
        pytest.skip(f"only {len(commands)} retained commands — corpus has rotated")

    caught = []
    for command in commands:
        try:
            argv = shlex.split(command)
        except ValueError:
            continue  # unbalanced quotes in a heredoc — never reaches argv either
        hit, reason = is_catastrophic(argv)
        if hit:
            caught.append((command[:120], reason))

    assert not caught, (
        f"{len(caught)} of {len(commands)} commands he has actually run would now "
        f"stop and ask: {caught[:5]}"
    )


# --------------------------------------------------------------------------- #
# 3. The prose copy that made this worth fixing twice
# --------------------------------------------------------------------------- #


@pytest.mark.tripwire
def test_the_model_is_not_told_a_list_that_a_new_member_falsifies() -> None:
    """The tool's DESCRIPTION is read by the model on every turn, and it used to
    enumerate the gated shapes — so growing the gated set silently made the
    model's own instructions wrong, in the direction of "that runs silently".

    The cure this repo mandates for two copies of one rule is to REMOVE the copy,
    not to sync it: the description now names the CLASSES, which a new member
    cannot falsify. This pins that it does not regrow into a list.
    """
    description = ShellTool().description
    assert "uninstall" in description.lower(), (
        "the model is not told that removing software needs approval"
    )
    prose = description.split("PYTHON:")[0]
    for member in ("apt", "dpkg", "systemctl", "pip", "snap", "brew", "npm"):
        # Word-boundary, not substring: "pip" sits inside "pipes" in the sentence
        # about shell syntax, and the first draft of this guard failed on it.
        assert not re.search(rf"\b{member}\b", prose), (
            f"the description enumerates {member!r} again — a list here goes stale "
            "the moment the table grows, and it goes stale in the unsafe direction"
        )


# --------------------------------------------------------------------------- #
# 4. End to end — it never spawns, and it SAYS SO where a check can read it
# --------------------------------------------------------------------------- #


@pytest.mark.tripwire
@pytest.mark.asyncio
async def test_an_uninstall_never_spawns_and_names_its_class_at_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The line this item's closing check greps for, pinned at its emitter.

    Read off the NAMED LOGGER rather than ``caplog``: ``configure_logging`` sets
    ``propagate = False`` on ``stackowl``, so a caplog assertion passes alone and
    fails in any session that configured logging first. That is measured, not
    hypothetical — it cost a previous item a full-suite run.

    The message must be a LITERAL at the call site. ``_gate_catastrophic``'s
    existing line carries the class only in a ``reason`` FIELD, which the closing
    check's own audit guard cannot see, and which ``rm -rf /`` would satisfy just
    as well — so it could never tell this fix from its predecessor.
    """
    import logging

    from stackowl.config.test_mode import TestModeGuard
    from stackowl.infra.observability import log
    from stackowl.infra.trace import TraceContext
    from stackowl.pipeline.services import StepServices, reset_services, set_services

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # Both spawners, because a learned tool takes the exec path and ShellTool the
    # shell one — patching only the one this test uses would let the other regress.
    captured: dict[str, bool] = {"called": False}

    async def _never(*_args: object, **_kwargs: object) -> object:
        captured["called"] = True
        raise AssertionError("spawned")

    monkeypatch.setattr(
        "stackowl.tools.system.shell.asyncio.create_subprocess_exec", _never
    )
    monkeypatch.setattr(
        "stackowl.tools.system.shell.asyncio.create_subprocess_shell", _never
    )

    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collect(level=logging.DEBUG)
    log.tool.addHandler(handler)
    token = set_services(StepServices(consent_gate=None))
    trace = TraceContext.start(session_key="123", interactive=False, channel="telegram")
    try:
        result = await ShellTool().execute(command="apt-get purge -y openssh-server")
    finally:
        TraceContext.reset(trace)
        reset_services(token)
        log.tool.removeHandler(handler)

    assert captured["called"] is False, "an uninstall reached a subprocess"
    assert result.success is False
    assert result.side_effect_committed is False

    named = [r for r in records if "REMOVES INSTALLED SOFTWARE" in r.getMessage()]
    assert named, (
        "nothing named the removal class in the log — the closing check for this "
        "item greps that literal, and a check whose evidence cannot exist reads "
        "OPEN forever"
    )
    assert named[0].levelno >= logging.INFO, (
        "production runs at INFO; a DEBUG line is evidence that never arrives"
    )

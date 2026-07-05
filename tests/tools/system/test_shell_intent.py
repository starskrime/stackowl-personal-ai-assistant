"""Per-invocation read/write intent for ShellTool.

``shell`` declared a STATIC ``action_severity="write"`` on every call, so a
read-only PROBE (``curl -sI``, ``test -f``, ``grep``) that exited non-zero was
classified identically to a failed destructive mutation — tripping the honest
give-up floor (``unachieved_effect``) even though the turn delivered a complete,
honest answer.

Fix: the CALLER declares ``intent`` per call. A declared read that crosses no
side-effect boundary reports ``side_effect_committed=False`` so a failed probe is
NOT counted as an unachieved effect. The declaration is not blindly trusted — a
command that structurally redirects stdout to a named file (an observable write)
is treated as a write regardless of the declared intent (anti-gaming, mirroring
the base Tool's demotion of self-asserted ``verified=True``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.tool_outcome_ledger import is_effectful_failure
from stackowl.tools.system.shell import ShellTool


@pytest.mark.asyncio
async def test_failed_read_probe_is_not_an_effectful_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A read-only probe that exits non-zero must not trip the honest floor."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    result = await ShellTool()(command="false", intent="read", workdir=str(tmp_path))

    assert result.success is False
    assert result.side_effect_committed is False
    # The ledger's single source of truth agrees: not an unachieved effect.
    assert (
        is_effectful_failure(
            "write", result.success, result.side_effect_committed, result.verified
        )
        is False
    )


@pytest.mark.asyncio
async def test_failed_command_without_intent_stays_effectful(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No declared intent ⇒ byte-identical to before: a failure still counts."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    result = await ShellTool()(command="false", workdir=str(tmp_path))

    assert result.success is False
    assert result.side_effect_committed is True
    assert (
        is_effectful_failure(
            "write", result.success, result.side_effect_committed, result.verified
        )
        is True
    )


@pytest.mark.asyncio
async def test_read_intent_refuted_by_stdout_redirect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A declared read that redirects stdout to a file is an observable write —
    the declaration is not trusted (anti-gaming)."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    result = await ShellTool()(
        command="echo hi > out.txt", intent="read", workdir=str(tmp_path)
    )

    assert result.success is True
    assert result.side_effect_committed is True  # refuted → treated as a write


@pytest.mark.asyncio
async def test_successful_read_probe_commits_no_side_effect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A declared read with no redirect crosses no side-effect boundary."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    result = await ShellTool()(command="echo hi", intent="read", workdir=str(tmp_path))

    assert result.success is True
    assert result.side_effect_committed is False

"""The verification primitive works. shell almost never gets to use it.

MEASURED 2026-09-01 against ``side_effect_ledger`` (1,959 real committed calls):

===============  ======  =====================  ==========================
tool             calls   ``artifact_path`` set  ``verified``
===============  ======  =====================  ==========================
``shell``        1,067   36 (3.4%)              None on 1,039 (97.4%)
``write_file``   39      —                      **True on 38 (97%)**
===============  ======  =====================  ==========================

The primitive is not broken. When it fires it is decisive — four of shell's 28
non-None verdicts are ``False``, each an exit-0 command that produced nothing.
It simply almost never fires.

THE CAUSE IS THE SCHEMA, NOT THE CHECK. ``write_file(path=...)`` names its effect
structurally, so verification is automatic. ``shell(command=...)`` names only a
command string, and the ONLY way ``artifact_path`` gets set is
``_redirect_target`` recognising an unambiguous stdout redirect. A heredoc, a
``python3 -c``, a ``tee``, a ``curl -o``, or any program that writes internally
leaves it None — so ``verified`` stays None and the turn ends on an unconfirmed
write.

Bakir's RCA verdict for shell:stop names exactly this: "the deliverable (the md
file write) was never re-read back, and the completion message was grounded in a
spawn/attempt rather than a fresh observation". Corroborated across the corpus:
of 207 ``stop`` turns that used tools, 152 wrote something and **139 of those 152
— 91% — performed no read of any kind after their last write.**

THE FIX IS NOT A NEW RE-READ STEP. One already exists and works. What was missing
is any way for an invocation to NAME its artifact in the forms a redirect cannot
express. ``expect_file`` is that — the same shape as the existing caller-declared
``intent`` param, feeding the SAME ``verify()`` seam.

IT CANNOT BE USED TO FAKE A PASS. ``verify_artifact`` requires the file to exist,
be non-empty, AND be newer than the call started, so naming a stale pre-existing
file fails. Declaring an effect you did not produce is a stronger failure signal
than declaring nothing, which is the point.

THE PRINCIPLE IS PRESERVED, NOT WEAKENED. shell's docstring says it "never
over-claims verification for an arbitrary command", and that stays true: nothing
is inferred. The caller states the effect, or there is still no verdict.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.tools.system.shell import ShellTool

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def _live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)


async def test_a_heredoc_write_can_now_be_verified(_live, tmp_path: Path) -> None:  # noqa: ANN001
    """The live shape: a write with no redirect anywhere. Before this, 1,039 of
    1,067 shell calls ended with no verdict at all."""
    result = await ShellTool()(
        command="printf 'the deliverable\\n' | tee report.md > /dev/null",
        workdir=str(tmp_path),
        expect_file="report.md",
    )
    assert result.success is True, result.error
    assert result.verified is True
    assert result.artifact_path == str(tmp_path / "report.md")


async def test_declaring_a_file_you_did_not_produce_FAILS(_live, tmp_path: Path) -> None:  # noqa: ANN001
    """The whole point. An exit-0 command that did not produce what it said it
    would is now verified=False instead of a silent false success."""
    result = await ShellTool()(
        command="true", workdir=str(tmp_path), expect_file="never_written.md",
    )
    assert result.success is True       # the self-report is preserved
    assert result.verified is False     # reality disagrees


async def test_a_STALE_file_does_not_verify(_live, tmp_path: Path) -> None:  # noqa: ANN001
    """The anti-gaming direction, and the reason this cannot become a rubber
    stamp: naming a file that already existed proves nothing about THIS run."""
    stale = tmp_path / "old.md"
    stale.write_text("written long before this call")
    import os, time  # noqa: E401
    old = time.time() - 3600
    os.utime(stale, (old, old))

    result = await ShellTool()(
        command="true", workdir=str(tmp_path), expect_file="old.md",
    )
    assert result.verified is False, (
        "a pre-existing file satisfied the check — expect_file would be a way to "
        "claim verification without producing anything"
    )


async def test_an_empty_file_does_not_verify(_live, tmp_path: Path) -> None:  # noqa: ANN001
    """Same contract the redirect path already has: created but empty is not
    produced."""
    result = await ShellTool()(
        command="touch empty.md", workdir=str(tmp_path), expect_file="empty.md",
    )
    assert result.verified is False


async def test_an_absolute_path_is_honoured(_live, tmp_path: Path) -> None:  # noqa: ANN001
    """Relative names resolve against workdir exactly as the redirect path does;
    an absolute one must be taken as given."""
    target = tmp_path / "abs.md"
    result = await ShellTool()(
        command=f"printf 'x\\n' > {target}", workdir=str(tmp_path),
        expect_file=str(target),
    )
    assert result.verified is True
    assert result.artifact_path == str(target)


async def test_without_expect_file_nothing_changes(_live, tmp_path: Path) -> None:  # noqa: ANN001
    """Back-compat, byte for byte. 1,067 recorded shell calls behaved this way and
    must continue to — this is the same assertion test_shell_verify.py already
    makes, restated here because it is what bounds the blast radius."""
    result = await ShellTool()(command="echo hi", workdir=str(tmp_path))
    assert result.success is True, result.error
    assert result.verified is None
    assert result.artifact_path is None


async def test_the_redirect_path_still_wins_on_its_own(_live, tmp_path: Path) -> None:  # noqa: ANN001
    """The existing structural detector must keep working with no declaration."""
    result = await ShellTool()(command="echo hello > out.txt", workdir=str(tmp_path))
    assert result.verified is True
    assert result.artifact_path == str(tmp_path / "out.txt")


def test_the_model_can_actually_see_it() -> None:
    """A FEATURE SHIPS ON. This is the exact failure D03.4 recorded — a result cap
    shipped with no tool declaring one, so it could never fire. If expect_file is
    not in the schema the model cannot set it, and this is decoration."""
    params = ShellTool().parameters
    props = params["properties"]  # type: ignore[index]
    assert "expect_file" in props, "the model is never offered expect_file"
    desc = str(props["expect_file"]["description"])  # type: ignore[index]
    assert "verif" in desc.lower(), (
        "the description does not tell the model what declaring this BUYS, so it "
        "has no reason to use it"
    )
    assert "expect_file" not in params.get("required", []), (
        "expect_file must stay optional — a required param would break every "
        "read-only shell call"
    )

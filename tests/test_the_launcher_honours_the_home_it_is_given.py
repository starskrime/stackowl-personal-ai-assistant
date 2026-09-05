"""D18.3 — the sanctioned restart path was instance-hostile, and silently.

`CLAUDE.md` mandates `./start.sh` as THE restart entry point. Measured 2026-09-05,
it was the one place the state-path discipline did not hold:

    line 34  runtime_dir="$HOME/.stackowl/runtime"
    line 41  nohup ... > "$HOME/.stackowl/manual_restart_stdout.log"
    line 44  "Tail ~/.stackowl/logs/stackowl.jsonl"

and worse, its orphan sweep matched on the PROCESS NAME —
`pgrep -f "python3? -m stackowl (start|__core__)"` — which is blind to which home a
process belongs to. So `STACKOWL_HOME=~/.stackowl-work ./start.sh` would have
**killed the running instance**, deleted ITS pid and socket, and overwritten ITS
stdout log, while every one of the platform's own 25 path accessors correctly
resolved into the new home. Isolation that is complete in `src/` and absent in the
launcher is not isolation.

THE SHAPE IS `CLAUDE.md`'s "same rule, one case short". The single-accessor rule is
real and holds across all of `src/`; the launcher is the one caller that reimplemented
the path by hand, and being a shell script is why nothing caught it — every guard this
repo owns reads Python.

This asserts the launcher derives its paths from STACKOWL_HOME. It does NOT assert
multi-instance is supported: that is an open question for the operator (see
ESC-136). It asserts only that the launcher cannot silently act on the wrong home.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_START = _ROOT / "start.sh"


@pytest.mark.tripwire
def test_no_shell_script_reimplements_the_home() -> None:
    """The rule reaches every shell file, because the root cause is the LANGUAGE.

    The single-accessor rule holds across all of `src/`; it broke in the two places
    written in bash, where no guard this repo owns could see it. Measured
    2026-09-05: `start.sh` (three paths) and `scripts/full_suite.sh:20` (the suite
    log) were the only two offenders out of 20 shell files. Fixing only the
    launcher would have left the same defect one file over — which is exactly the
    shape `CLAUDE.md` calls "fix the architecture, not the example".
    """
    root = _ROOT
    offenders: dict[str, list[str]] = {}
    scanned = 0
    for path in sorted(root.rglob("*.sh")):
        if any(part in {".git", ".venv", "do_not_push_to_git_research_only"} for part in path.parts):
            continue
        scanned += 1
        hits = [
            line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
            and re.search(r'\$HOME/\.stackowl|~/\.stackowl|/home/[^/]+/\.stackowl', line)
        ]
        if hits:
            offenders[str(path.relative_to(root))] = hits

    assert scanned >= 10, f"expected the real shell surface, scanned {scanned}"
    assert not offenders, (
        f"shell script(s) re-deriving the home: {offenders}\n"
        "D18.3: ask StackowlHome for it — "
        "`uv run python -c 'from stackowl.paths import StackowlHome; ...'`. "
        "Every path in src/ derives from one accessor; a shell script that "
        "hardcodes ~/.stackowl acts on a different instance than the process it "
        "launches, and no Python guard can see it."
    )


@pytest.mark.tripwire
def test_the_launcher_has_no_hardcoded_home() -> None:
    text = _START.read_text(encoding="utf-8")
    body = [
        line for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert body, "start.sh has no executable lines — parsed wrong"

    hardcoded = [
        line.strip() for line in body
        if re.search(r'\$HOME/\.stackowl|~/\.stackowl|/home/[^/]+/\.stackowl', line)
    ]
    assert not hardcoded, (
        f"start.sh hardcodes the default home: {hardcoded}\n"
        "D18.3: derive it from STACKOWL_HOME, as every path accessor in src/ does. "
        "A launcher that writes to one home while the process it launches uses "
        "another will delete the wrong pid file and kill the wrong process."
    )


@pytest.mark.tripwire
def test_the_orphan_sweep_is_scoped_to_one_instance() -> None:
    """The sweep must not kill by process name alone.

    `pgrep -f 'python3? -m stackowl'` matches EVERY instance on the box. The sweep
    exists for a real incident (an orphaned `__core__` holding port 8766), so it
    must stay — but it must decide which home a candidate belongs to before
    killing it.
    """
    text = _START.read_text(encoding="utf-8")

    assert "STACKOWL_HOME" in text, (
        "start.sh never mentions STACKOWL_HOME, so it cannot know which instance "
        "it is acting on"
    )
    kills = [
        line.strip() for line in text.splitlines()
        if re.search(r"^\s*kill\s+-", line) and not line.lstrip().startswith("#")
    ]
    assert kills, "no kill lines found — the parser is wrong, not the script"
    for line in kills:
        assert "instance_pids" in line or "leftover" in line, (
            f"a kill that does not draw from the scoped PID list: {line}"
        )


def test_the_launcher_still_sweeps_orphans() -> None:
    """The incident that put the sweep there must stay covered.

    2026-07-22: an orphaned `__core__` reparented to init kept port 8766 bound
    after a normal stop+start. Removing the sweep to make the script tidier would
    reopen that.
    """
    text = _START.read_text(encoding="utf-8")
    assert "pgrep" in text, "the orphan sweep was removed — see start.sh's own header"
    assert "__core__" in text, "the sweep no longer matches orphaned core processes"

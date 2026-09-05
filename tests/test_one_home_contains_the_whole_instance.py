"""D18.3 — the property that makes an isolated instance possible, asserted.

`docs/stackowl.yaml.example` now tells an operator that `STACKOWL_HOME` roots the
whole instance. That sentence is only true while it stays true, and it is exactly
the kind of claim that rots silently: one new accessor that forgets to derive from
`home()` and the documentation becomes a lie, with the symptom appearing far away
as "my second instance is writing into the first one's database".

MEASURED 2026-09-05, which is why the claim is worth making at all: with
`STACKOWL_HOME` pointed at a temp directory, **all 25 accessors returning a Path
resolve inside it — 0 escapes**, including `db_path`, `logs_dir`, `config_file`,
`core_socket` and `pid_file`.

THIS IS NOT AN ASSERTION THAT MULTI-INSTANCE IS SUPPORTED. It is not: five
process-level global names still collide (the keychain service string, the Telegram
bot token, the service-unit filename, the webhook port, the MCP port), and whether
to solve them is the operator's call — ESC-136. This asserts only the STORAGE half,
which is the half that is already complete, and which `tests/conftest.py` depends on
to keep test runs out of the operator's real home.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

_PROBE = """
import inspect, sys
from pathlib import Path
from stackowl.paths import StackowlHome
root = Path(sys.argv[1])
escapes = []
count = 0
for name, member in inspect.getmembers(StackowlHome):
    if name.startswith("_") or not callable(member):
        continue
    try:
        if len(inspect.signature(member).parameters) != 0:
            continue
        value = member()
    except Exception:
        continue
    if not isinstance(value, Path):
        continue
    count += 1
    if not str(value).startswith(str(root)):
        escapes.append(f"{name}={value}")
print(count)
print("|".join(escapes))
"""


@pytest.mark.tripwire
def test_every_path_accessor_is_contained_by_the_home(tmp_path: Path) -> None:
    """Run in a SUBPROCESS, because the environment is the thing under test.

    Setting STACKOWL_HOME in-process would leak into every later test in the
    session, and the accessors are imported once — the subprocess is what makes
    this measure the real resolution rather than a monkeypatched one.
    """
    env = dict(os.environ, STACKOWL_HOME=str(tmp_path))
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _PROBE, str(tmp_path)],
        capture_output=True, text=True, env=env, check=False,
    )
    assert result.returncode == 0, f"probe failed: {result.stderr[-400:]}"

    # NOT .strip(): with zero escapes the second line is empty, and stripping it
    # away makes the unpack fail — the pass case would look like a broken probe.
    lines = result.stdout.split("\n")
    count_line, escapes_line = lines[0], lines[1]
    count = int(count_line)
    escapes = [e for e in escapes_line.split("|") if e]

    assert count >= 20, (
        f"only {count} Path accessors found — the probe is measuring the wrong "
        "thing, and a zero-escape result over too small a denominator is not a pass"
    )
    assert not escapes, (
        f"{len(escapes)} path(s) escape STACKOWL_HOME: {escapes}\n"
        "D18.3: every persistent path must derive from StackowlHome.home(), or a "
        "second instance writes into the first one's state and the test suite "
        "writes into the operator's real home. Derive it from home() or workspace()."
    )


def test_the_documentation_makes_this_claim() -> None:
    """The guard and the sentence it protects must not drift apart.

    If the example config stops telling operators about STACKOWL_HOME, this guard
    is protecting a promise nobody is making — and the capability goes back to
    being undiscoverable, which is the defect D18.3 actually found.
    """
    root = Path(__file__).resolve().parents[1]
    example = (root / "docs" / "stackowl.yaml.example").read_text(encoding="utf-8")
    assert "STACKOWL_HOME" in example, (
        "the example config no longer documents STACKOWL_HOME — the variable that "
        "roots the entire instance is undiscoverable again"
    )


def test_the_accessor_reads_the_environment_at_call_time() -> None:
    """The mechanism the whole property rests on."""
    from stackowl.paths import StackowlHome

    source = inspect.getsource(StackowlHome.home)
    assert "environ" in source, (
        "StackowlHome.home() no longer reads the environment at call time — "
        "nothing else in this file can hold if that changes"
    )

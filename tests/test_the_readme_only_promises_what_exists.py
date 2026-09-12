"""A README rots because nothing checks it. This checks it.

WHY THIS EXISTS. Measured 2026-09-06: this repository had **no README at all**,
while the operator's standing rule is that "people will download it from GitHub
and set it up on their machines". The platform is genuinely installable — the CLI
ships `init`, `setup --minimal/--demo/--channel`, `validate-config`, `start`,
`install-service`, `backup`, `export`, `import` — so what was missing was not the
capability but any pointer to it. That is defect shape 5, built-but-not-wired,
applied to the front door.

WHY THE GUARD IS THE ACTUAL DELIVERABLE, not the prose. A README is a set of
claims about the tree, written once and then diverging from it silently — the
same failure mode as an escalation whose premise ages, or a `partial` stage with
no runnable check. Both of those were cured in this programme by making the claim
EXECUTABLE (`premise_check`, `closing_check`), and this is the same cure for the
same disease: every command the README tells a new user to run must exist, every
path it names must be present, and the Python version it states must be the one
`pyproject.toml` requires.

WHAT THIS DELIBERATELY DOES NOT DO. It does not check that the instructions
WORK on a clean machine — no test here can, and claiming otherwise would be the
"a fixture that cannot show the bug proves nothing" failure. It checks the
weaker, honest property: the README never names something the tree does not
have. A command that exists can still be wrong; a command that does not exist is
wrong for certain, and that is the failure a README actually dies of.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_README = _ROOT / "README.md"


#: Paths the README tells a user to run or open. Matched WITHOUT requiring
#: backticks, because the ones that matter most sit inside fenced ```bash
#: blocks — the vacuity control caught the backtick-only version finding a
#: single path and passing anyway.
_PATH_RE = re.compile(r"(?<![\w/])(\./[A-Za-z0-9_./-]+|scripts/[A-Za-z0-9_./-]+|docs/[A-Za-z0-9_./-]+\.md)")


def _paths_named() -> set[str]:
    return set(_PATH_RE.findall(_readme()))


def _readme() -> str:
    assert _README.exists(), "the repository has no README — a fresh clone has no front door"
    return _README.read_text(encoding="utf-8")


@pytest.mark.tripwire
def test_every_stackowl_command_it_names_exists() -> None:
    """The core promise. `stackowl <cmd>` in the README must be a real command."""
    from stackowl.__main__ import app

    real = {c.name for c in app.registered_commands if c.name}
    real |= {g.name for g in getattr(app, "registered_groups", []) if g.name}
    # Typer falls back to the function name when `name=` is not given.
    real |= {
        (c.callback.__name__.replace("_", "-") if c.callback else "")
        for c in app.registered_commands
    }
    real.discard("")

    named = set(re.findall(r"stackowl\s+([a-z][a-z0-9-]+)", _readme()))
    named -= {"init"} & set()  # nothing exempt; kept explicit for the next reader
    missing = sorted(n for n in named if n not in real)

    assert not missing, (
        f"the README tells a new user to run commands that do not exist: {missing}\n"
        f"(real commands: {sorted(real)})"
    )


@pytest.mark.tripwire
def test_every_path_it_names_is_present() -> None:
    """A README that points at a moved script sends a new user into a wall."""
    named = _paths_named()
    missing = sorted(p for p in named if not (_ROOT / p.lstrip("./")).exists())

    assert not missing, f"the README names paths that do not exist: {missing}"


@pytest.mark.tripwire
def test_the_python_version_matches_pyproject() -> None:
    """The one claim a new user cannot recover from getting wrong, and the one
    most likely to drift — it lives in two files by nature."""
    meta = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requires = meta["project"]["requires-python"]          # e.g. ">=3.13"
    version = re.search(r"(\d+\.\d+)", requires).group(1)  # type: ignore[union-attr]

    assert version in _readme(), (
        f"pyproject requires Python {requires} and the README does not say {version}"
    )


def test_the_guard_sees_a_real_population() -> None:
    """VACUITY CONTROL. If the regexes matched nothing, all three assertions
    above would pass over empty sets — which is exactly how a guard becomes
    decoration.

    IT USED TO DEMAND `>= 4` PATHS AND THAT FLOOR FAILED FOR CORRECT WORK. The
    README named two design documents; the programme that produced them was
    retired on 2026-09-12 and the count fell to three, so the control went red
    for a change that removed exactly what it was meant to remove. A floor under
    a population the work exists to CHANGE fails the day the work succeeds, and
    lowering it only defers that — 4 becomes 3 becomes 2.

    So the extraction is proven against a string written HERE, where the expected
    answer is known exactly, and the live README only has to be non-empty.
    """
    sample = (
        "Run `stackowl serve` and `stackowl health`.\n"
        "See ./start.sh, scripts/tripwires.sh and docs/anything.md.\n"
    )
    assert set(re.findall(r"stackowl\s+([a-z][a-z0-9-]+)", sample)) == {"serve", "health"}
    assert set(_PATH_RE.findall(sample)) == {
        "./start.sh", "scripts/tripwires.sh", "docs/anything.md",
    }, "the path extraction no longer finds what it is built to find"

    commands = set(re.findall(r"stackowl\s+([a-z][a-z0-9-]+)", _readme()))
    paths = _paths_named()

    assert commands, "the README names no commands — the extraction went blind on it"
    assert paths, "the README names no paths — the extraction went blind on it"

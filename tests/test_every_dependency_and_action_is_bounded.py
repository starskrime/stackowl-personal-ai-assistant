"""D17.5 — a dependency needs a ceiling, and a CI action needs a SHA.

MEASURED 2026-09-05, before adopting the policy:

    dependencies in pyproject.toml       44
      with an upper bound                 2   (playwright, pypdf)
      without                            42
    `uses:` lines in .github/workflows     8
      pinned to a 40-char commit SHA      0
      pinned to a MUTABLE TAG             8   (@v4, @v2, @v5)

`uv.lock` IS committed, so an install today is reproducible — which is why the
dependency half is about the RESOLUTION step rather than the install: an
unbounded `>=` means the next `uv lock --upgrade`, or adding one package, will
accept a brand-new major, including a release published minutes ago by whoever
just compromised the maintainer's account.

THE ACTION HALF IS THE SHARPER ONE. A tag is mutable. `actions/checkout@v4` is
whatever that tag points at when the job runs, executing in CI with the
repository's credentials — the exact shape of the worm campaigns this policy was
written after. A lockfile does not cover it, because CI never reads the lockfile
for its actions.

TWO CLAUSES ARE VACUOUS HERE and are recorded so nobody re-adopts them as work:
there are no git-URL dependencies (0 in pyproject) and no CI-only `pip install`
lines. Both are satisfied by having nothing to satisfy.

The bounds were derived from the LOCKED versions, so the existing `uv.lock`
still satisfies every specifier — the policy is adopted without re-resolving the
world.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PYPROJECT = _ROOT / "pyproject.toml"
_WORKFLOWS = _ROOT / ".github" / "workflows"

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
#: A requirement line: name, optional extras, then the specifier.
_DEP_RE = re.compile(r'^\s*"([A-Za-z0-9_.\-]+(?:\[[^\]]+\])?)\s*([^"]*)",?\s*$')


def _dependencies() -> list[tuple[str, str]]:
    """(name, specifier) for every dependency line in pyproject."""
    out: list[tuple[str, str]] = []
    inside = False
    for line in _PYPROJECT.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(("dependencies = [", "dev = [", "browser = [")) or (
            stripped.endswith("= [") and "dependencies" in stripped
        ):
            inside = True
            continue
        if inside and stripped == "]":
            inside = False
            continue
        if not inside:
            continue
        m = _DEP_RE.match(line)
        if m and m.group(2).strip():
            out.append((m.group(1), m.group(2).strip()))
    return out


def _action_uses() -> list[tuple[str, str]]:
    """(workflow file, uses-value) for every `uses:` in every workflow."""
    out: list[tuple[str, str]] = []
    for wf in sorted(_WORKFLOWS.glob("*.yml")) + sorted(_WORKFLOWS.glob("*.yaml")):
        for line in wf.read_text(encoding="utf-8").splitlines():
            m = re.search(r"uses:\s*(\S+)", line)
            if m:
                out.append((wf.name, m.group(1)))
    return out


@pytest.mark.tripwire
def test_every_dependency_has_an_upper_bound() -> None:
    deps = _dependencies()
    assert len(deps) > 30, f"expected the real dependency list, parsed {len(deps)}"

    unbounded = [f"{n}{s}" for n, s in deps if "<" not in s and "==" not in s]
    assert not unbounded, (
        f"dependency without a ceiling: {unbounded}.\n"
        "D17.5: `>=floor,<next_major`. An unbounded `>=` lets the next resolve "
        "accept a brand-new major — including a release published minutes ago by "
        "whoever compromised the maintainer."
    )


@pytest.mark.tripwire
def test_every_action_is_pinned_to_a_commit_sha() -> None:
    uses = _action_uses()
    assert len(uses) >= 5, f"expected the real workflow set, parsed {len(uses)}"

    unpinned = [
        f"{wf}: {u}" for wf, u in uses
        if "@" not in u or not _SHA_RE.match(u.split("@", 1)[1])
    ]
    assert not unpinned, (
        f"action pinned to a mutable ref: {unpinned}.\n"
        "D17.5: pin to a 40-char commit SHA. A tag is whatever it points at when "
        "the job runs, and it runs with the repository's credentials — the shape "
        "of the worm campaigns this policy was written after."
    )


def test_the_pinned_action_still_says_which_version_it_is() -> None:
    """A bare SHA is unreadable. Every pin keeps its human version in a comment,
    or the next person cannot tell an upgrade from a downgrade."""
    for wf in sorted(_WORKFLOWS.glob("*.yml")):
        for line in wf.read_text(encoding="utf-8").splitlines():
            if "uses:" in line and "@" in line:
                assert "#" in line, f"{wf.name}: pinned action with no version comment: {line.strip()}"


def test_the_vacuous_clauses_are_still_vacuous() -> None:
    """Two clauses of the policy have nothing to bind. If that changes, they stop
    being free and someone must apply them."""
    text = _PYPROJECT.read_text(encoding="utf-8")

    assert "git+" not in text, "a git-URL dependency appeared — pin it to a 40-char SHA"
    for wf in sorted(_WORKFLOWS.glob("*.yml")):
        assert "pip install" not in wf.read_text(encoding="utf-8"), (
            f"{wf.name} gained a CI pip install — pin it exactly"
        )

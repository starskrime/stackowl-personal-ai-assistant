"""D18.7 — eight architectural boundary checkers existed and NONE of them ran.

`scripts/boundaries/` holds the B1-B9 checks. Measured 2026-09-05:

    b1  circular imports              50 cycles     exit 1
    b2  file length > 300 lines      196 files      exit 1
    b3  ASCII-only regex / stopwords   4 sites      exit 1
    b4  cross-platform                 0            exit 0   <- fixed and WIRED here
    b5  silent/bare except            145 sites     exit 1
    b6  mypy --strict                  many         exit 1
    b8  network imports in embeddings  0            exit 0
    b9  blocking I/O in handlers       0            exit 0

**None was in `scripts/tripwires.sh`, and their only CI home was a
`.pre-commit-config.yaml` hook still running `cd v2 && ...` — the v2->root
migration deleted `v2/` on 2026-06-17.** So five checkers exited 1 for months and
nothing read a single verdict.

FOUR OF THESE ENFORCE STANDING OPERATOR RULES, which is why the silence matters:
cross-platform code (b4), "every except logs" (b5), "never hardcode English
keyword lists" (b3), and the file-size discipline (b2).

WHY b4 WAS NEVER WIRED IS THE ROOT CAUSE, AND IT IS NOT NEGLECT. It was
unwireable: it fired on `os.path.expandvars` (portable — pathlib has no
equivalent), on a `hasattr(signal, "SIGHUP")`-guarded call, on a security REGEX
that DETECTS `/tmp` staging, on help text, and on a container-side mount. A guard
that fails on correct code teaches its reader to ignore it, and ignoring it is
precisely what happened. It was made sound first, and only then wired.

This test does NOT run the checkers — b6 shells `mypy --strict` over the tree and
would make the gate minutes long. It asserts the weaker, cheap property that was
actually missing: every checker is either WIRED (and the gate then enforces its
greenness on every commit) or explicitly DECLARED as known-red with a reason. A
new checker cannot be added and quietly ignored, and a declaration cannot outlive
its file.
"""

from __future__ import annotations

import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_BOUNDARIES = _ROOT / "scripts" / "boundaries"
_GATE = _ROOT / "scripts" / "tripwires.sh"

#: Known-red and deliberately NOT gating yet. Each says what it would cost, because
#: "known red" with no reason is how a red check becomes permanent. Deliberately no
#: violation COUNTS here: a count would be the change-detector D18.6 just banned —
#: it would break on every honest improvement and teach people to bump the number.
_KNOWN_RED: dict[str, str] = {
    "b1": "circular imports across stackowl.* packages — real architectural debt; "
          "breaking the cycles is a refactor programme, not a gate flip.",
    "b2": "source files over 300 lines. The tree has long outgrown the limit and "
          "its own docstring still says 'v2/src/', so the rule predates the "
          "migration. Whether 300 is still the intended limit is a decision.",
    "b3": "ASCII-only regexes without re.UNICODE. SMALL and it maps to a standing "
          "rule ('never hardcode English keyword lists') — the cheapest of the "
          "five to close.",
    "b5": "except blocks with no log call and no re-raise. Maps to the MANDATORY "
          "'every except logs' rule, and is the largest of the five.",
    "b6": "mypy --strict. The repo's baseline is non-strict; strict is a separate "
          "programme, not an item.",
}


def _checkers() -> set[str]:
    return {p.stem for p in _BOUNDARIES.glob("b*.py")}


def _wired() -> set[str]:
    gate = _GATE.read_text(encoding="utf-8")
    return {name for name in _checkers() if f"boundaries/{name}.py" in gate}


@pytest.mark.tripwire
def test_every_boundary_checker_is_wired_or_declared_red() -> None:
    checkers = _checkers()
    assert len(checkers) >= 5, f"expected the real boundary suite, found {checkers}"

    unaccounted = sorted(checkers - _wired() - set(_KNOWN_RED))
    assert not unaccounted, (
        f"boundary checker(s) neither wired into the gate nor declared red: "
        f"{unaccounted}.\n"
        "D18.7: a checker that runs nowhere is not a check. Either add it to "
        "scripts/tripwires.sh (make it green first — a guard that fires on correct "
        "code is one nobody wires), or declare it in _KNOWN_RED with what closing "
        "it would cost."
    )


@pytest.mark.tripwire
def test_the_cross_platform_check_is_actually_in_the_gate() -> None:
    """The operator's standing rule needs an enforcer that runs, not one that exists.

    Asserted by name rather than left to the generic rule above, because this is the
    one the item was about: it was green-able all along and simply never gated.
    """
    assert "b4" in _wired(), (
        "scripts/boundaries/b4.py is no longer in scripts/tripwires.sh. The "
        "cross-platform rule is a standing operator requirement and it went "
        "unenforced for months precisely because nothing ran this."
    )
    assert "b4" not in _KNOWN_RED, "b4 is wired; it must not also be declared red"


def test_no_declaration_outlives_its_checker() -> None:
    stale = sorted(name for name in _KNOWN_RED if not (_BOUNDARIES / f"{name}.py").exists())
    assert not stale, (
        f"declared red but the checker is gone: {stale}. A list that outlives its "
        "subjects stops describing anything."
    )


def test_no_ci_path_points_at_the_deleted_v2_tree() -> None:
    """The migration is the cause; this is the property it violated.

    `v2/` was moved to the repo root on 2026-06-17. Every CI and packaging path
    that still names it is dead, and dead-but-present is what let five red
    checkers go unread.
    """
    offenders: dict[str, list[str]] = {}
    for rel in (".pre-commit-config.yaml", "scripts/tripwires.sh"):
        path = _ROOT / rel
        if not path.exists():
            continue
        hits = [
            line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if "v2/" in line and not line.lstrip().startswith("#")
        ]
        if hits:
            offenders[rel] = hits
    for wf in sorted((_ROOT / ".github" / "workflows").glob("*.yml")):
        hits = [
            line.strip() for line in wf.read_text(encoding="utf-8").splitlines()
            if "v2/" in line and not line.lstrip().startswith("#")
        ]
        if hits:
            offenders[str(wf.relative_to(_ROOT))] = hits

    assert not offenders, (
        f"CI/packaging path(s) still pinned to the deleted v2/ tree: {offenders}"
    )

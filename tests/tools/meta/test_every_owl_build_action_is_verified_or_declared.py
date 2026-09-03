"""An action nobody verifies FLOORS the turn that succeeded. Four times now.

`OwlBuildTool.verify` enumerates the actions it can observe and returns ``None``
for the rest. ``None`` DEFAULT-DENIES at the overclaim gate, so an action with no
verifier turns a completed piece of work into "The capability that failed:
owl_build" and the answer is thrown away.

THE FOUR:

    2026-08-16  rename   a rename that WORKED reported as failed
    2026-08-19  clamp    19 overclaims across 50 runs; nothing was broken
    2026-08-22  edit     a landed edit verified as unknown
    2026-09-03  grant    HE ASKED FOR A JOB-MARKET AGENT. `create` and `grant`
                         both exited success=True, owls.jobmarket landed at
                         04:29:52.445 — and the turn floored at 04:30:20 with
                         `overclaim.detected failed_capability=owl_build`. The
                         loop recovered it, routed the recovery to the owl the
                         turn had just built, and he was told it already existed.

Each fix added ONE action to the verifier. None of them stopped the NEXT action
falling into the same silent default — which is why this test exists instead of a
fifth comment. The default is now inverted: an action must be verified or
explicitly declared unverifiable, and adding one without deciding which is a red
test rather than a floored turn weeks later.
"""

from __future__ import annotations

import inspect

import pytest

from stackowl.tools.meta.owl_build import (
    UNVERIFIABLE_ACTIONS,
    OwlBuildTool,
    _VALID_ACTIONS,
)


@pytest.mark.tripwire
def test_every_action_is_either_verified_or_declared_unverifiable() -> None:
    src = inspect.getsource(OwlBuildTool._verify_by_action)
    # `create` is verified on the other branch of verify() — it stamps
    # artifact_path and is re-read against the live registry there.
    verified = {a for a in _VALID_ACTIONS if f'action == "{a}"' in src} | {"create"}

    undecided = sorted(set(_VALID_ACTIONS) - verified - UNVERIFIABLE_ACTIONS)

    assert not undecided, (
        "these owl_build actions are neither verified nor declared unverifiable, "
        "so each one FLOORS the turn that ran it — the exact defect that cost him "
        f"a job-market agent on 2026-09-03:\n  {undecided}\n"
        "Add a branch to _verify_by_action, or name it in UNVERIFIABLE_ACTIONS "
        "with the reason nothing observable proves it landed."
    )


def test_the_declared_list_only_holds_REAL_actions() -> None:
    """The other direction: a declaration for an action the tool no longer accepts
    is residue, and it would silently excuse a future action of the same name."""
    ghosts = sorted(UNVERIFIABLE_ACTIONS - set(_VALID_ACTIONS))
    assert not ghosts, ghosts


def test_grant_is_VERIFIED_not_declared_away() -> None:
    """The control that matters. This test would also pass if someone "fixed"
    2026-09-03 by adding `grant` to UNVERIFIABLE_ACTIONS — which would stop the
    floor and give up on ever knowing whether a grant landed. A grant HAS an
    observable effect: the owl may now use the tools."""
    assert "grant" not in UNVERIFIABLE_ACTIONS
    assert 'action == "grant"' in inspect.getsource(OwlBuildTool._verify_by_action)

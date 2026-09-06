"""No evidence to judge is not the same as judged and found wanting.

WHY THIS EXISTS, measured 2026-09-06 over the retained log window.

`[dna] coordinator.promote: shadow gate REJECTED` fired **69 times** at ERROR.
Read at face value: sixty-nine DNA candidates were evaluated and found to
regress the owl. Read the fields:

    {"owl": "librarian", "n_replayed": 2, "consecutive_non_regressions": 0,
     "n_consecutive_required": 3, "failures": []}

**63 of the 69 are arithmetically impossible** — `n_replayed` is smaller than
the number of consecutive non-regressions required, so the verdict was settled
before any replay ran. ELEVEN of them replayed *nothing at all* (`n_replayed:
0`). Not one of the 69 was a rejection the candidate could have avoided by being
better.

THE VALIDATOR ALREADY KNOWS, AND ALREADY SAYS SO — AT INFO. Its cold-start
branch logs `[shadow] validate: exit — cold start, insufficient held-out sample`
and returns `passed=False` deliberately: "fails CLOSED (not a vacuous pass, not
a crash)". That decision is right and is NOT what this changes. What changes is
the CALLER, which turns "I could not judge" into "REJECTED" at ERROR — a
conclusion the callee explicitly declined to draw.

IT IS NOT HISTORICAL. A different cause of the same rejection (replays hitting
`ToolCallLeakError` because the isolation harness wires no tools) was found and
fixed on 2026-09-01; promotions start appearing 09-02 and there are six. The
cold-start rejections continue every single day regardless — **five on
2026-09-06.**

WHAT IT COSTS. An operator reading five REJECTED-at-ERROR lines a day concludes
the self-improvement loop is producing bad hypotheses. The truth is that those
owls have no replayable history yet, which is a completely different problem with
a completely different fix. This is the same shape as the deliver step claiming
"answer not delivered" for a turn that owed nobody an answer: a caller asserting
an OUTCOME when what it observed was an ABSENCE.

The genuine-regression path is untouched and still ERROR — Story 2.7's decision
that "a gate rejection must be visible without a human specifically going
looking for it" is about a candidate that was actually judged, and a cold start
never was.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest import mock

import pytest

from stackowl.owls import evolution as evolution_mod
from stackowl.owls.dna import OwlDNA
from stackowl.owls.shadow_validator import ShadowValidationResult

pytestmark = pytest.mark.asyncio


async def _none(*_a: object, **_k: object) -> None:
    return None


class TestTheResultCanTellTheTwoApart:
    def test_a_cold_start_is_flagged_as_such(self) -> None:
        """THE DEFECT ITSELF. Without this the caller must re-derive the branch
        from `n_replayed` and a threshold it does not own — a second, drifting
        copy of a judgement the validator already made."""
        result = ShadowValidationResult(
            passed=False, consecutive_non_regressions=0, n_replayed=2, failures=(),
            cold_start=True,
        )

        assert result.cold_start is True

    def test_a_measured_regression_is_not_a_cold_start(self) -> None:
        """THE CONTROL. A candidate that WAS replayed and did regress must stay
        distinguishable, or this fix would hide the failure it exists to clarify."""
        result = ShadowValidationResult(
            passed=False, consecutive_non_regressions=1, n_replayed=3,
            failures=({"input_text": "x", "why": "regressed"},),
            cold_start=False,
        )

        assert result.cold_start is False
        assert result.failures, "a real regression still carries its evidence"

    def test_cold_start_defaults_to_False(self) -> None:
        """Every existing construction site means 'this was actually judged'.
        Defaulting the other way would silently reclassify real rejections."""
        result = ShadowValidationResult(
            passed=True, consecutive_non_regressions=3, n_replayed=3, failures=(),
        )

        assert result.cold_start is False


class TestTheValidatorSetsItOnTheBranchItAlreadyTakes:
    def test_the_cold_start_branch_marks_the_result(self) -> None:
        """ONE SOURCE. The validator knows which branch it took; the caller must
        not re-derive it from `n_replayed < required`, because that comparison
        would then live in two places and could disagree."""
        import ast
        import inspect

        from stackowl.owls import shadow_validator

        tree = ast.parse(inspect.getsource(shadow_validator))
        marked = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "ShadowValidationResult"
            and any(kw.arg == "cold_start" for kw in n.keywords)
        ]

        assert marked, (
            "no ShadowValidationResult in the validator sets cold_start — the "
            "caller would have to guess which branch produced the verdict"
        )


class TestTheBEHAVIOURIsIdenticalOnlyTheMESSAGEDiffers:
    """THE REGRESSION THIS ITEM NEARLY SHIPPED, pinned by DRIVING the code.

    The first cut wrote `if cold_start: ... elif not passed: ...`. FR-10's
    restore-and-reaffirm and the `return False` live inside that block, so a cold
    start would have fallen THROUGH to `_persist_dna(safe_dna)` and promoted an
    unvalidated candidate — worse than the noisy log it was fixing, and the exact
    opposite of the fail-closed guarantee.

    AND THE FIRST VERSION OF THIS TEST WAS DECORATION. It walked the AST and
    asserted "restore" and "return False" appear in the branch body — which stays
    true when an early `return False` is inserted ABOVE the restore. Both mutants
    SURVIVED it. Presence in a block is not reachability, and only running the
    code can tell the difference. These tests drive the real method.
    """

    async def _run(self, *, cold_start: bool) -> tuple[bool, list[str], list[str]]:
        """Drive the real promotion path with a scripted validator.

        Returns (verdict, what-was-persisted, log-messages).
        """
        from stackowl.owls.evolution import EvolutionCoordinator

        persisted: list[str] = []
        restored: list[str] = []

        class _Validator:
            n_consecutive_required = 3

            async def validate(self, *_a: object, **_k: object) -> ShadowValidationResult:
                return ShadowValidationResult(
                    passed=False,
                    consecutive_non_regressions=0,
                    n_replayed=0 if cold_start else 3,
                    failures=() if cold_start else ({"why": "regressed"},),
                    cold_start=cold_start,
                )

        class _Store:
            async def checkpoint(self, *_a: object, **_k: object) -> str:
                return "ckpt-1"

            async def restore(self, _kind: str, name: str, _cid: str) -> dict:
                restored.append(name)
                return OwlDNA().model_dump()

        coord = EvolutionCoordinator.__new__(EvolutionCoordinator)
        coord._shadow_validator = _Validator()          # type: ignore[attr-defined]
        coord._learning_store = _Store()                # type: ignore[attr-defined]
        coord._owl_registry = SimpleNamespace(get=lambda _n: None)  # type: ignore[attr-defined]

        async def _persist(name: str, _dna: object) -> None:
            persisted.append(name)

        coord._persist_dna = _persist                   # type: ignore[attr-defined]
        coord._db = None                                # type: ignore[attr-defined]

        manifest = SimpleNamespace(name="scout", dna=OwlDNA())
        with mock.patch.object(evolution_mod, "read_authored_dna", new=_none), \
             mock.patch.object(evolution_mod, "bound_dna", new=lambda *a, **k: OwlDNA()), \
             mock.patch.object(evolution_mod, "apply_dna_overlay", new=lambda *a, **k: None):
            verdict = await coord._checkpoint_validate_and_promote(
                manifest, OwlDNA(), evolution_source="test", signal=None,  # type: ignore[arg-type]
            )
        return verdict, persisted, restored

    async def test_a_cold_start_still_RESTORES_and_refuses(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The fail-closed guarantee, driven rather than inspected. A cold start
        must restore the checkpoint and return False — never fall through to the
        promotion below."""
        with caplog.at_level(logging.INFO):
            verdict, persisted, restored = await self._run(cold_start=True)

        assert verdict is False, "a cold start PROMOTED the candidate"
        assert restored == ["scout"], f"the checkpoint was not restored: {restored}"
        msgs = [r.getMessage() for r in caplog.records]
        assert any("no verdict" in m for m in msgs), msgs
        assert not any("shadow gate REJECTED" in m for m in msgs), (
            "a cold start was still reported as a rejected candidate"
        )

    async def test_a_real_regression_still_says_REJECTED_at_ERROR(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The control. Story 2.7's loud case must survive this change."""
        with caplog.at_level(logging.INFO):
            verdict, _persisted, restored = await self._run(cold_start=False)

        assert verdict is False
        assert restored == ["scout"], "a real regression stopped restoring"
        loud = [r for r in caplog.records if "shadow gate REJECTED" in r.getMessage()]
        assert loud, "the genuine rejection stopped being reported"
        assert loud[0].levelno >= logging.ERROR, loud[0].levelname



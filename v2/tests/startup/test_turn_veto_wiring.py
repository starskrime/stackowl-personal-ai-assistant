"""Orchestrator wires the turn-veto coherence judge (concurrent-msg §5.5 backlog).

The concurrent-message slice shipped with ``TurnRouter(clarify_classifier,
turn_veto=None)`` — the stage-2 coherence veto deferred. This wiring test asserts
the boot path now constructs the ``TurnRouter`` with a REAL ``turn_veto`` bound to
the classifier's ``is_steer_incoherent`` coherence judge (the SECOND gate that
saves live-steer from incoherent blends), so ``turn_veto`` is no longer ``None``.

Source-inspection style (mirrors ``test_dna_hydration_wiring`` /
``test_authored_capture_wiring``): the assembly is a long boot method, so we assert
the wiring is present in source rather than booting the whole gateway.
"""

import inspect

from stackowl.startup import orchestrator


def test_orchestrator_wires_turn_veto_to_coherence_judge() -> None:
    src = inspect.getsource(orchestrator)
    # The deferred placeholder is gone — turn_veto is a real callable now.
    assert "turn_veto=None" not in src
    # The TurnRouter is constructed with a turn_veto bound to the coherence judge.
    assert "is_steer_incoherent" in src
    assert "turn_veto=" in src

"""The gate matched a culprit NAME where the registry declares the PROPERTY.

Bakir, 2026-09-09: "the steps finishes very fast … delegation of tasks, getting
compacted info, and efficient planning not setupped and designed correctly."

MEASURED over every retained log: 150 overclaims detected. The gate has actuators for
exactly two culprits — and BOTH are classifier TAGS rather than tool names
(`retrieval`, `scheduling_commit`), as `delivery_gate.py:1265-1276` says itself. So
every real tool-name culprit fell through to the honest floor no matter how safe it was
to redo:

    retrieval          108   -> actuator, 43 corrected
    web_fetch           16   -> declared `read`, no actuator, floor
    owl_build           12   -> `consequential`, 8 re-fulfil attempts, 0 succeeded
    todo                 3   -> declared `read`, no actuator, floor
    browser_navigate     3   -> declared `read`, no actuator, floor
    send_message         2   -> `consequential`, floor
    read_file            1   -> declared `read`, no actuator, floor

That is 23 overclaims by tools the registry declares READ — the exact property
`tools/base.py:414` exists to express: `_is_retry_safe_severity`, "True only for a
declared READ-severity tool — the one case where re-running is safe". The gate never
asked it. The user got "I couldn't fully complete this" where a safe re-run was
available, which is Bakir's own recorded objection in this very file: "I do not want
agent to record, I want agent to do."

AND THE 0/8 ON owl_build WAS NOT THIS DEFECT. All eight re-fulfil failures named
`owl_build`, whose writer and verifier used different field maps until DEBT-250 fixed
it hours earlier — no re-run could ever have verified. Two separate causes wearing one
symptom; this test is about the read-severity half.
"""

from __future__ import annotations

import inspect

import pytest

from stackowl.pipeline import delivery_gate
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.registry import ToolRegistry


@pytest.fixture
def _wired():
    token = set_services(StepServices(tool_registry=ToolRegistry.with_defaults()))
    try:
        yield
    finally:
        reset_services(token)


@pytest.mark.usefixtures("_wired")
def test_a_read_severity_tool_is_safe_to_redo() -> None:
    """The real registry, not a double: a double that drifts from the declared
    severity would test nothing about the decision actually made in production."""
    for name in ("web_fetch", "browser_navigate", "read_file"):
        assert delivery_gate._culprit_is_retry_safe(name) is True, (  # noqa: SLF001
            f"{name} is declared read-severity and is still being confessed"
        )


@pytest.mark.usefixtures("_wired")
def test_a_write_or_consequential_tool_is_NOT_redone_here() -> None:
    """`owl_build` and `send_message` can commit a side effect twice. They keep the
    floor unless the separate `effects_measured_absent` branch proves nothing landed."""
    for name in ("owl_build", "send_message"):
        assert delivery_gate._culprit_is_retry_safe(name) is False  # noqa: SLF001


@pytest.mark.usefixtures("_wired")
def test_the_two_classifier_TAGS_keep_their_own_actuators() -> None:
    """`retrieval` and `scheduling_commit` are not tool names — they are classifier
    verdicts with bespoke paths above. Routing them here would replace a specific
    remedy with a generic one."""
    assert delivery_gate._culprit_is_retry_safe("retrieval") is False  # noqa: SLF001
    assert delivery_gate._culprit_is_retry_safe("scheduling_commit") is False  # noqa: SLF001


@pytest.mark.usefixtures("_wired")
def test_an_unknown_culprit_fails_CLOSED() -> None:
    """The burden of proof stays on the claim — the same rule the write branch states.
    An unregistered name must keep the floor, never earn a re-run by default."""
    assert delivery_gate._culprit_is_retry_safe("no_such_tool_at_all") is False  # noqa: SLF001
    assert delivery_gate._culprit_is_retry_safe("") is False  # noqa: SLF001


def test_an_unwired_registry_fails_CLOSED() -> None:
    """No services at all is the boot-order case, and it must not open the gate."""
    token = set_services(StepServices())
    try:
        assert delivery_gate._culprit_is_retry_safe("web_fetch") is False  # noqa: SLF001
    finally:
        reset_services(token)


@pytest.mark.tripwire
def test_the_gate_actually_asks_before_it_floors() -> None:
    """WIRED, NOT DECORATION. A severity helper nothing calls would leave all 23
    read-severity overclaims confessing exactly as before."""
    src = inspect.getsource(delivery_gate)
    # The CALL, not the definition. `_culprit_is_retry_safe(culprit` also matches
    # `def _culprit_is_retry_safe(culprit: str)` near the top of the file, so the first
    # draft's span covered the whole module and swept in the write branch.
    idx_call = src.index('_culprit_is_retry_safe(culprit or "")')
    idx_floor = src.index("floor = (")

    assert idx_call < idx_floor, "the severity check runs after the floor is chosen"
    # The span to inspect is MY branch only — from the severity call to where the
    # WRITE branch begins. The first draft asserted `overclaim.refulfilled` was absent
    # between the call and the floor, which fails on correct code: the write branch
    # legitimately sits in that range and legitimately emits it.
    idx_write = src.index("effects_measured_absent or ()", idx_call)
    # CODE ONLY. Searching raw source catches PROSE: the branch's own comment says
    # "not the write branch's `overclaim.refulfilled`", and the first draft failed on
    # that sentence. What is asserted here is which message the branch EMITS.
    read_branch = "\n".join(
        line for line in src[idx_call:idx_write].splitlines()
        if not line.lstrip().startswith("#")
    )

    assert "overclaim.redone" in read_branch, (
        "a successful redo no longer records itself — the claim becomes uncheckable"
    )
    assert "overclaim.refulfilled" not in read_branch, (
        "the read path reuses the write branch's message, so a count cannot say which "
        "branch acted"
    )
    assert idx_write < idx_floor, "the write branch moved after the floor"


class _RecordingActuator:
    """Records that the gate asked for a corrective re-run, and declines it.

    Returning None keeps the floor, so this asserts only the thing the structural
    test could not: that the branch is REACHED.
    """

    def __init__(self) -> None:
        self.corrections: list[str] = []

    async def run_corrective(self, *, original, correction: str):  # noqa: ANN001, ANN201
        self.corrections.append(correction)
        return None


@pytest.mark.asyncio
async def test_the_read_severity_branch_is_REACHED_not_merely_present(monkeypatch) -> None:
    """THE STRUCTURAL TEST ABOVE WAS VACUOUS AND A MUTATION PROVED IT.

    Disabling the branch with `if False and _culprit_is_retry_safe(...)` left all six
    source-reading assertions green: they check that the call and the message EXIST,
    which a dead branch satisfies perfectly. That is the ships-as-decoration shape the
    file is about, reproduced inside its own guard.

    This one drives `surface_overclaim_gate` with a read-severity culprit and asserts
    the corrective re-run was actually ASKED FOR. It fails on the `if False` mutation,
    which is the only property worth pinning here.
    """
    from stackowl.pipeline.state import PipelineState

    actuator = _RecordingActuator()
    token = set_services(
        StepServices(tool_registry=ToolRegistry.with_defaults(), retry_actuator=actuator)  # type: ignore[arg-type]
    )
    state = PipelineState(
        trace_id="t-readsev", session_key="s", input_text="fetch the page",
        channel="telegram", owl_name="secretary", pipeline_step="",
    )
    # The culprit decision itself is not under test — the BRANCH is.
    monkeypatch.setattr(delivery_gate, "_is_overclaim", lambda _s: (True, "web_fetch"))
    try:
        await delivery_gate.surface_overclaim_gate(state)
    finally:
        reset_services(token)

    assert actuator.corrections, (
        "a read-severity overclaim did not reach the corrective re-run — the branch is "
        "present in the source but never taken"
    )
    assert "web_fetch" in actuator.corrections[0]

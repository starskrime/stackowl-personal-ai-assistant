"""A run that CANNOT reach the host may proceed unattended. One that can may not.

OPERATOR DECISION, ESC-150 (2026-09-05): grant confined runs unattended, and notify
each time. He took the broad rule with an observation channel over a narrower
network-off-only variant.

WHY IT WAS ASKED. Measured across every retained log: `execute_code` consent decisions
are **27 deny, 1 allow** — every refusal on the `rca` lane with the same reason,
"always-ask and no human is attached". The single allow came through Telegram, where a
human approved it, and it then ran successfully on bwrap. So the platform built a
confinement subsystem (21 modules, two backends, seccomp, cgroups) whose entire purpose
is that untrusted code can run SAFELY WITHOUT A HUMAN — and then refused it precisely
when no human was there. The safe path was shut while `shell`, unconfined by design,
ran 205 times and launched an interpreter 33 detected times.

THE DISTINCTION THIS FILE EXISTS TO PIN, and it is the thing that would have gone wrong.
`code_execution` is a CATEGORY with TWO members, and they are not alike:

    execute_code   NEVER runs on the host. With no sandbox wired it REFUSES
                   (`execute_code.py:215`), and SandboxBackend invariant 1 forbids
                   degrading to a bare subprocess. Confinement is contract-backed.
    claude_code    "runs shell commands in `workdir` on the HOST (no isolated
                   sandbox — unlike execute_code)" — its own docstring.

Granting the CATEGORY would therefore have made unattended HOST execution automatic,
which is the opposite of what was approved. The carve-out is keyed on the CONTRACT a
tool holds, never on the category and never on a name alone.

DELIVERY IS BEST-EFFORT AND THE RECORD IS NOT. The proactive bridge drops an event when
no recipient resolves (its "honest-recipient rail"), so a notification cannot be the only
trace — a fresh clone with no Telegram configured would then execute silently. Every
auto-grant is therefore LOGGED at INFO unconditionally, and the ping rides the existing
delivery seam on top. Failing the grant closed when undeliverable was rejected: it would
re-create the original defect in a new costume, and would break any install that has not
configured a channel.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.tools.consent import ConsentRequest, ConsentScope, runs_confined


def test_execute_code_is_recognised_as_confined() -> None:
    """Contract-backed: it cannot reach the host even if it wanted to."""
    assert runs_confined("execute_code") is True


def test_claude_code_is_NOT_confined() -> None:
    """THE SAFETY TEST. It is in the same consent category and runs on the HOST.
    If this ever returns True, unattended host execution becomes automatic."""
    assert runs_confined("claude_code") is False


@pytest.mark.parametrize("tool", ["shell", "write_file", "process", "git", "", None])
def test_nothing_else_is_confined(tool: str | None) -> None:
    """Fails closed for anything it has not been told about."""
    assert runs_confined(tool) is False


@pytest.mark.asyncio
async def test_a_confined_run_is_granted_when_nobody_is_attached() -> None:
    """The 27 denials this exists to end."""
    from stackowl.tools.consent import AutonomousPrompter

    scope = await AutonomousPrompter().prompt(ConsentRequest(
        tool_name="execute_code", channel="rca", session_key="incident-x",
        allow_relaxation=False,
    ))
    assert scope is ConsentScope.ONCE


@pytest.mark.asyncio
async def test_an_UNCONFINED_code_tool_is_still_refused() -> None:
    """`claude_code` shares the always-ask category and runs on the host, so the
    autonomous path must still refuse it. This is the assertion that keeps the
    carve-out narrow."""
    from stackowl.tools.consent import AutonomousPrompter

    scope = await AutonomousPrompter().prompt(ConsentRequest(
        tool_name="claude_code", channel="rca", session_key="incident-x",
        allow_relaxation=False,
    ))
    assert scope is ConsentScope.DENY


@pytest.mark.asyncio
async def test_the_grant_is_recorded_at_INFO_unconditionally(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Production runs at INFO, and proactive delivery is best-effort — the bridge
    drops an event with no resolvable recipient. So the LOG is the guaranteed trace;
    without it a fresh clone would execute unattended with nothing to read."""
    from stackowl.tools.consent import AutonomousPrompter

    with caplog.at_level(logging.INFO):
        await AutonomousPrompter().prompt(ConsentRequest(
            tool_name="execute_code", channel="rca", session_key="incident-x",
            allow_relaxation=False,
        ))

    # MATCH THE GRANT LINE SPECIFICALLY. This first searched for any message
    # containing "confined" and was VACUOUS: `_emit_confined_grant`'s own fallback
    # ("no event bus wired") also says confined, at INFO, so demoting the GRANT
    # record to DEBUG left the test green. Mutation testing found it; the assertion
    # now names the sentence whose absence would mean silent execution.
    hits = [
        r for r in caplog.records
        if "confined execution granted" in r.getMessage().lower()
    ]
    assert hits, (
        "an unattended confined execution left no INFO record of the GRANT itself — "
        "proactive delivery is best-effort, so this line is the only guaranteed trace"
    )
    assert hits[0].levelno >= logging.INFO, (
        f"the grant is recorded at {hits[0].levelname}; production runs at INFO"
    )


@pytest.mark.tripwire
def test_the_notification_event_has_ONE_spelling() -> None:
    """The bridge's own rule: "Built from the PUBLISHERS' own constants, never from
    literals repeated here. A subscription is a contract with the module that emits,
    and this file used to hold an independent second spelling of it."
    """
    import pathlib

    from stackowl.tools.consent import CONFINED_EXEC_GRANTED_EVENT

    bridge = (
        pathlib.Path(__file__).resolve().parents[2]
        / "src" / "stackowl" / "notifications" / "event_bridge.py"
    ).read_text(encoding="utf-8")

    assert "CONFINED_EXEC_GRANTED_EVENT" in bridge, (
        "the bridge does not subscribe the confined-execution event, so the "
        "notification he asked for would never be delivered"
    )
    assert f'"{CONFINED_EXEC_GRANTED_EVENT}"' not in bridge, (
        "the bridge spells the event name as a literal — import the publisher's "
        "constant instead, or a rename leaves it subscribing to a name nobody sends"
    )


# --------------------------------------------------------------------------- #
# Attendance — the gate that keeps a wiring fault from becoming an auto-grant   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_live_channel_with_no_prompter_still_REFUSES() -> None:
    """THE REGRESSION THIS EXISTS TO STOP, found by `tests/startup` after the first
    version of the carve-out shipped.

    `RoutingPrompter` routes to `AutonomousPrompter` whenever a channel has no
    prompter registered — which covers TWO different worlds it cannot tell apart:
    the rca lane, where nobody can be asked, and a live Slack socket whose prompter
    B3 failed to wire, where the operator is sitting right there. Keyed on the
    confinement contract alone, the carve-out granted both, turning a wiring fault
    into a silent unattended execution on an interactive channel and breaking the
    C-6 invariant that an unwired channel cannot escalate an always-ask action.
    """
    from stackowl.channels.registry import ChannelRegistry
    from stackowl.tools.consent import AutonomousPrompter

    class _LiveAdapter:
        channel_name = "slack"

        async def send(self, chunks: object) -> None: ...
        async def send_text(self, text: str, **kw: object) -> None: ...
        async def receive(self) -> object: ...

    registry = ChannelRegistry.instance()
    registry.register(_LiveAdapter())  # type: ignore[arg-type]
    try:
        scope = await AutonomousPrompter().prompt(ConsentRequest(
            tool_name="execute_code", channel="slack", session_key="slack:1",
            allow_relaxation=False,
        ))
        assert scope is ConsentScope.DENY
    finally:
        registry.reset()


@pytest.mark.asyncio
async def test_an_unreadable_registry_counts_as_ATTENDED(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FAILS CLOSED ON THE INSTRUMENT, not just on the answer.

    `_gateway_channels()` returned the empty set both when the registry was EMPTY and
    when it could not be READ. Provenance reads that as "no vouching origin" and
    refuses — correct. This carve-out reads absence as "nobody is there" and grants —
    so one unreadable registry would have failed closed for one caller and OPEN for
    the other, from the same value. Hence `_gateway_channels_or_unknown`, and hence
    this test drives the REAL registry rather than the helper: a double standing in
    front of the guard cannot test the guard.
    """
    from stackowl.channels import registry as reg
    from stackowl.tools.consent import AutonomousPrompter

    def _boom() -> object:
        raise RuntimeError("registry gone")

    monkeypatch.setattr(reg.ChannelRegistry, "instance", staticmethod(_boom))

    scope = await AutonomousPrompter().prompt(ConsentRequest(
        tool_name="execute_code", channel="rca", session_key="incident-x",
        allow_relaxation=False,
    ))
    assert scope is ConsentScope.DENY, (
        "attendance could not be established and the run was granted anyway"
    )


def test_unknown_and_empty_are_DISTINGUISHABLE(monkeypatch: pytest.MonkeyPatch) -> None:
    """The distinction the split helper exists to preserve, asserted directly so a
    later 'simplification' back to one function is caught here rather than in a
    consent decision.

    A first draft of this test asserted `x is not None or True`, which is true for
    every possible value — it would have passed against the unsplit helper it was
    written to protect. Both branches are now driven against the real registry.
    """
    from stackowl.channels import registry as reg
    from stackowl.tools import consent as mod

    # READ, and holding nothing: empty set, NOT None.
    reg.ChannelRegistry.instance().reset()
    assert mod._gateway_channels_or_unknown() == frozenset()

    # UNREADABLE: None from the attendance view, empty from the provenance view.
    def _boom() -> object:
        raise RuntimeError("registry gone")

    monkeypatch.setattr(reg.ChannelRegistry, "instance", staticmethod(_boom))
    assert mod._gateway_channels_or_unknown() is None, (
        "an unreadable registry is indistinguishable from an empty one — the "
        "confined carve-out would read it as 'nobody is there' and grant"
    )
    assert mod._gateway_channels() == frozenset(), (
        "the provenance view must still collapse unknown to empty and refuse"
    )

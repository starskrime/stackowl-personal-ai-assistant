"""A new surface feeds the one conversation. It never builds a second one.

WHY THIS EXISTS — D13.2's Ask: *"Adopt as a written rule before building surface #4?"*

The reference platform states it as an architectural rule: its dashboard **embeds**
`hermes --tui` through a PTY rather than reimplementing the chat in React. Structured UI
around it is allowed; a second transcript or composer is not. Anything added to the TUI
appears in the dashboard automatically.

**We need the same guarantee and cannot use the same mechanism**, because our conversation
does not live in the TUI to be embedded. It lives behind the `ChannelAdapter` seam: a
surface turns input into an `IngressMessage` and renders `ResponseChunk`s, and sessions,
transcript, tools, model calls and slash routing all sit behind that line. So our version of
the rule is about the SEAM rather than about embedding a terminal.

MEASURED 2026-09-06, and the property already holds — which is exactly why it is worth
locking before surface #4 rather than after:

  * `IngressMessage` is constructed in 10 modules: seven under `channels/`, plus the three
    intake-path modules named below.
  * There is ONE turn entry point, `_dispatch_turn`, and `pipeline/durable/agent_task.py`
    shares it behind a generic `_IntakeAdapter` rather than opening a second one.
  * Two `channels/` modules DO construct a `PipelineState` — `slack/slash_bridge.py` and
    `telegram/command_buttons.py` — and both are the narrow legitimate case: they dispatch
    a slash command through `CommandRegistry`, with zero references to any pipeline runner.
    telegram's own comment says it replays "through the SAME CommandRegistry.dispatch path a
    typed slash command uses".

That distinction is the rule's whole content. A surface may build the minimal state a
COMMAND needs, because commands are dispatched through one registry that every surface
shares. A surface may never build state to run a TURN, because that is a second
conversation, and the second one is where the transcript, the session and the tool loop
quietly fork.

The payoff is the same one the PTY embedding buys them: **anything added to the pipeline
appears on every surface automatically.**
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src" / "stackowl"

#: The intake path — the three modules outside `channels/` that legitimately construct an
#: `IngressMessage`, each for a stated reason. Declared rather than inferred, so a FOURTH
#: one is a decision someone makes on purpose instead of a drift nobody sees.
_INTAKE_PATH = {
    "gateway/scanner.py": "defines IngressMessage and scans inbound text for signals",
    "runtime/message_bridge.py": "carries a message across the gateway/core process split",
    "startup/orchestrator.py": "the one turn entry point, which re-forms messages on resume",
}


def _modules_containing(needle: str) -> set[str]:
    out: set[str] = set()
    for path in _SRC.rglob("*.py"):
        if needle in path.read_text(encoding="utf-8"):
            out.add(str(path.relative_to(_SRC)))
    return out


class TestOnlyASurfaceSpeaksIngress:
    @pytest.mark.tripwire
    def test_ingress_is_built_by_channels_or_the_declared_intake_path(self) -> None:
        """A module outside both is a new front door to the conversation."""
        builders = _modules_containing("IngressMessage(")
        strays = {
            m for m in builders
            if not m.startswith("channels/") and m not in _INTAKE_PATH
        }

        assert not strays, (
            "these modules construct an IngressMessage but are neither a surface nor the "
            f"declared intake path — each is a second way in: {sorted(strays)}"
        )

    def test_the_check_sees_a_real_population(self) -> None:
        """VACUITY CONTROL. A discovery that silently returned nothing would pass the
        assertion above by measuring an empty set."""
        builders = _modules_containing("IngressMessage(")

        assert len(builders) >= 8, f"only found {len(builders)} ingress builders"
        assert sum(1 for m in builders if m.startswith("channels/")) >= 5


class TestASurfaceNeverRunsATurn:
    @pytest.mark.tripwire
    def test_a_channel_building_turn_state_must_dispatch_a_COMMAND(self) -> None:
        """THE RULE'S ACTUAL CONTENT.

        Building a `PipelineState` inside `channels/` is allowed for exactly one reason:
        dispatching a slash command through the registry every surface shares. Building one
        to drive the pipeline is a second conversation — a second transcript and a second
        session — which is the sprawl this rule exists to prevent.
        """
        runner_markers = ("run_pipeline", "_dispatch_turn", "execute_step", "Backend(")
        offenders: dict[str, str] = {}
        for module in sorted(_modules_containing("PipelineState(")):
            if not module.startswith("channels/"):
                continue  # autonomous drivers legitimately run turns; they are not surfaces
            text = (_SRC / module).read_text(encoding="utf-8")
            if not re.search(r"CommandRegistry|registry\.dispatch", text):
                offenders[module] = "builds turn state without dispatching a command"
            elif any(marker in text for marker in runner_markers):
                offenders[module] = "a surface reaching into the pipeline runner"

        assert not offenders, (
            "a surface is building its own conversation rather than feeding the one that "
            f"exists: {offenders}"
        )

    def test_the_two_known_command_bridges_are_still_the_only_ones(self) -> None:
        """Not a rule, a canary. These two are the narrow legitimate case; a THIRD is
        not necessarily wrong, but it is worth a human noticing, because each one is a
        surface reaching one step past the ingress seam."""
        channel_state_builders = {
            m for m in _modules_containing("PipelineState(") if m.startswith("channels/")
        }

        assert channel_state_builders == {
            "channels/slack/slash_bridge.py",
            "channels/telegram/command_buttons.py",
        }, f"the set of channel modules building turn state changed: {channel_state_builders}"


class TestTheRuleIsWrittenDownWhereItIsRead:
    @pytest.mark.tripwire
    def test_process_md_carries_the_rule(self) -> None:
        """A rule nobody reads is not adopted. PROCESS.md is what the loop reads when it
        asks how work is done here; the Ask was specifically to ADOPT it as written."""
        text = (_ROOT / "docs" / "reference-mapping" / "PROCESS.md").read_text(
            encoding="utf-8"
        )

        assert "A surface never owns the conversation" in text, (
            "the rule is enforced by tests but written nowhere a human would find it"
        )

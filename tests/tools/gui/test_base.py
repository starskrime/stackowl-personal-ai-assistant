"""GuiAdapter ABC contract — abstract methods + a FakeGuiAdapter test double.

S1 ships NO real adapter; this proves the ABC's shape: the abstract methods
cannot be instantiated bare, and a conforming double honours the contract
(real-display probe result, redact-or-refuse capture, gate-before-act perform).
"""

from __future__ import annotations

import pytest

from stackowl.tools.gui.base import GuiAdapter, GuiAvailability, GuiPlatform
from stackowl.tools.gui.models import ActionResult, CaptureResult
from stackowl.tools.gui.safety import EmergencyStop, evaluate_action
from stackowl.tools.gui.schema import CaptureMode, GuiAction


class FakeGuiAdapter(GuiAdapter):
    """A pure in-memory adapter double — no OS calls. Honours the contract."""

    def __init__(self, *, available: bool = True, can_redact: bool = True) -> None:
        self._available = available
        self._can_redact = can_redact
        self._stop = EmergencyStop()
        self.performed: list[str] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def platform(self) -> GuiPlatform:
        return "linux"

    @property
    def supports_som(self) -> bool:
        return True

    @property
    def supports_input(self) -> bool:
        return True

    async def is_available(self) -> GuiAvailability:
        # Structured probe result — never raises (the contract).
        return GuiAvailability.up() if self._available else GuiAvailability.down("no attended display")

    async def capture(self, *, mode: CaptureMode = "som") -> CaptureResult:
        # Redact-or-refuse: only surface a frame flagged redacted on success.
        return CaptureResult(frame=b"\x89PNG", width=800, height=600, redacted=self._can_redact)

    async def perform(self, action: GuiAction) -> ActionResult:
        # Gate-before-act: refuse anything the safety gate would not allow.
        decision = evaluate_action(action, os_name=self.platform, emergency_stop=self._stop)
        if decision.refused:
            return ActionResult(ok=False, action=action.action, detail="refused by gate")
        self.performed.append(action.action)
        return ActionResult(ok=True, action=action.action)


class TestAbstractness:
    def test_cannot_instantiate_bare_abc(self) -> None:
        with pytest.raises(TypeError):
            GuiAdapter()  # type: ignore[abstract]

    def test_availability_helpers(self) -> None:
        assert GuiAvailability.up().available
        down = GuiAvailability.down("headless")
        assert not down.available and down.reason == "headless"


@pytest.mark.asyncio
class TestAvailability:
    async def test_probe_up(self) -> None:
        assert (await FakeGuiAdapter().is_available()).available

    async def test_probe_down_no_display(self) -> None:
        out = await FakeGuiAdapter(available=False).is_available()
        assert not out.available and out.reason


@pytest.mark.asyncio
class TestCaptureContract:
    async def test_redacted_capture(self) -> None:
        cap = await FakeGuiAdapter().capture()
        assert cap.redacted is True

    async def test_capture_without_redaction_flag(self) -> None:
        cap = await FakeGuiAdapter(can_redact=False).capture()
        assert cap.redacted is False  # caller/gate then refuses this


@pytest.mark.asyncio
class TestPerformGate:
    async def test_blocked_combo_refused_in_perform(self) -> None:
        adapter = FakeGuiAdapter()
        res = await adapter.perform(GuiAction(action="key", keys="ctrl+alt+delete"))
        assert not res.ok and "fake" not in adapter.performed

    async def test_allowed_action_performs(self) -> None:
        adapter = FakeGuiAdapter()
        res = await adapter.perform(GuiAction(action="click", element=1))
        assert res.ok and adapter.performed == ["click"]

    async def test_emergency_stop_blocks_perform(self) -> None:
        adapter = FakeGuiAdapter()
        adapter._stop.engage()
        res = await adapter.perform(GuiAction(action="click", element=1))
        assert not res.ok

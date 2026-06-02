"""GuiAction schema validation — dual targeting, discriminator, frozen/extra-forbid."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.tools.gui.schema import GuiAction


class TestDualTargeting:
    def test_click_by_element_ok(self) -> None:
        a = GuiAction(action="click", element=5)
        assert a.element == 5
        assert a.x is None and a.y is None

    def test_click_by_pixel_ok(self) -> None:
        a = GuiAction(action="click", x=100, y=200)
        assert (a.x, a.y) == (100, 200)
        assert a.element is None

    def test_click_both_targets_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not both"):
            GuiAction(action="click", element=5, x=10, y=20)

    def test_click_no_target_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires a target"):
            GuiAction(action="click")

    def test_partial_pixel_rejected(self) -> None:
        with pytest.raises(ValidationError, match="BOTH x and y"):
            GuiAction(action="click", x=10)

    @pytest.mark.parametrize("action", ["double_click", "right_click", "move", "set_value"])
    def test_targeted_actions_require_target(self, action: str) -> None:
        kwargs: dict[str, object] = {"action": action}
        if action == "set_value":
            kwargs["value"] = "x"
        with pytest.raises(ValidationError):
            GuiAction(**kwargs)  # type: ignore[arg-type]


class TestDrag:
    def test_drag_element_to_element_ok(self) -> None:
        a = GuiAction(action="drag", element=1, to_element=2)
        assert a.element == 1 and a.to_element == 2

    def test_drag_pixel_to_pixel_ok(self) -> None:
        a = GuiAction(action="drag", x=1, y=2, to_x=3, to_y=4)
        assert (a.to_x, a.to_y) == (3, 4)

    def test_drag_missing_destination_rejected(self) -> None:
        with pytest.raises(ValidationError, match="destination"):
            GuiAction(action="drag", element=1)

    def test_drag_both_source_rejected(self) -> None:
        with pytest.raises(ValidationError, match="source"):
            GuiAction(action="drag", element=1, x=1, y=2, to_element=2)


class TestNonTargetedActions:
    def test_capture_default_mode(self) -> None:
        assert GuiAction(action="capture").mode == "som"

    def test_capture_rejects_target(self) -> None:
        with pytest.raises(ValidationError, match="does not take a screen target"):
            GuiAction(action="capture", element=1)

    def test_type_requires_text(self) -> None:
        with pytest.raises(ValidationError, match="non-empty text"):
            GuiAction(action="type")

    def test_type_ok(self) -> None:
        assert GuiAction(action="type", text="hello").text == "hello"

    def test_key_requires_keys(self) -> None:
        with pytest.raises(ValidationError, match="keys combo"):
            GuiAction(action="key")

    def test_key_ok(self) -> None:
        assert GuiAction(action="key", keys="ctrl+s").keys == "ctrl+s"

    def test_set_value_requires_value(self) -> None:
        with pytest.raises(ValidationError, match="requires a value"):
            GuiAction(action="set_value", element=1)

    def test_scroll_requires_direction(self) -> None:
        with pytest.raises(ValidationError, match="requires a direction"):
            GuiAction(action="scroll", element=1)

    def test_scroll_ok(self) -> None:
        a = GuiAction(action="scroll", element=1, direction="down")
        assert a.direction == "down" and a.amount == 3


class TestDiscriminatorAndFrozen:
    def test_bad_action_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GuiAction(action="explode")  # type: ignore[arg-type]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            GuiAction(action="click", element=1, bogus="x")  # type: ignore[call-arg]

    def test_frozen(self) -> None:
        a = GuiAction(action="click", element=1)
        with pytest.raises(ValidationError):
            a.element = 9  # type: ignore[misc]

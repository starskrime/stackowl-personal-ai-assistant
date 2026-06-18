"""GUI result models — frozen / extra-forbid + helpers."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.tools.gui.models import ActionResult, CaptureResult, ElementBounds, UIElement


class TestElementBounds:
    def test_center(self) -> None:
        assert ElementBounds(x=10, y=20, width=100, height=40).center() == (60, 40)

    def test_frozen(self) -> None:
        b = ElementBounds(x=0, y=0, width=1, height=1)
        with pytest.raises(ValidationError):
            b.x = 5  # type: ignore[misc]


class TestUIElement:
    def test_construct(self) -> None:
        e = UIElement(id=3, role="button", label="OK", bounds=ElementBounds(x=0, y=0, width=10, height=10))
        assert e.id == 3 and e.value is None

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            UIElement(  # type: ignore[call-arg]
                id=1, role="x", bounds=ElementBounds(x=0, y=0, width=1, height=1), bogus=1
            )


class TestCaptureResult:
    def test_defaults(self) -> None:
        c = CaptureResult(frame=b"\x89PNG", width=800, height=600)
        assert c.elements == () and c.redacted is False and c.is_local_vision is False

    def test_frozen(self) -> None:
        c = CaptureResult(frame=b"x", width=1, height=1)
        with pytest.raises(ValidationError):
            c.redacted = True  # type: ignore[misc]


class TestActionResult:
    def test_construct(self) -> None:
        r = ActionResult(ok=True, action="click", detail="clicked #3", duration_ms=12.0)
        assert r.ok and r.action == "click"

    def test_frozen(self) -> None:
        r = ActionResult(ok=True, action="click")
        with pytest.raises(ValidationError):
            r.ok = False  # type: ignore[misc]

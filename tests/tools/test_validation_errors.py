"""Tests for :mod:`stackowl.tools.validation_errors`."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from stackowl.tools.validation_errors import format_validation_error


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    schedule: str | None = None


def test_names_every_bad_field_no_pydantic_jargon() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _Args(action=1, schedule=2)  # type: ignore[arg-type]

    msg = format_validation_error(excinfo.value, "cronjob")

    assert "'action'" in msg
    assert "'schedule'" in msg
    assert "literal_error" not in msg
    assert "errors.pydantic.dev" not in msg

"""Story 4.3 — ``CommandSpec``'s validator: ``reversible`` <=> ``undo_command_type``.

AC1: "its ``CommandSpec`` has a type, a typed payload model, a severity from
``authz/``, a reversibility and an undo command type" — this file proves the
"non-null iff reversible" rule that makes the last two fields a real pair
rather than two independent optional strings.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from stackowl.authz.severity import ALL_SEVERITIES
from stackowl.commands.spec.command_spec import CommandSpec


class _Payload(BaseModel):
    job_id: str


def test_reversible_requires_an_undo_command_type():
    with pytest.raises(ValidationError):
        CommandSpec(
            command_type="x.reversible_no_undo",
            payload_model=_Payload,
            severity="write",
            reversible=True,
            undo_command_type=None,
        )


def test_irreversible_forbids_an_undo_command_type():
    with pytest.raises(ValidationError):
        CommandSpec(
            command_type="x.irreversible_with_undo",
            payload_model=_Payload,
            severity="write",
            reversible=False,
            undo_command_type="x.undo",
        )


def test_reversible_with_undo_is_valid():
    spec = CommandSpec(
        command_type="x.reversible",
        payload_model=_Payload,
        severity="write",
        reversible=True,
        undo_command_type="x.undo",
    )
    assert spec.reversible is True
    assert spec.undo_command_type == "x.undo"


def test_irreversible_with_no_undo_is_valid():
    spec = CommandSpec(
        command_type="x.irreversible",
        payload_model=_Payload,
        severity="consequential",
        reversible=False,
    )
    assert spec.reversible is False
    assert spec.undo_command_type is None


@pytest.mark.parametrize("severity", sorted(ALL_SEVERITIES))
def test_every_authz_severity_is_accepted(severity: str):
    spec = CommandSpec(
        command_type=f"x.{severity}",
        payload_model=_Payload,
        severity=severity,
        reversible=False,
    )
    assert spec.severity == severity


def test_an_undeclared_severity_is_refused():
    with pytest.raises(ValidationError):
        CommandSpec(
            command_type="x.bad_severity",
            payload_model=_Payload,
            severity="delete-everything",
            reversible=False,
        )


def test_the_spec_is_frozen():
    spec = CommandSpec(
        command_type="x.frozen",
        payload_model=_Payload,
        severity="read",
        reversible=False,
    )
    with pytest.raises(ValidationError):
        spec.severity = "write"  # type: ignore[misc]

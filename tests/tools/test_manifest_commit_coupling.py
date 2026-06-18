"""ToolManifest.commit_coupling field — the honesty axis (Story D1 §6.1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.tools.base import ToolManifest


def _manifest(**kw: object) -> ToolManifest:
    base = dict(name="t", description="d", parameters={})
    base.update(kw)
    return ToolManifest(**base)  # type: ignore[arg-type]


def test_commit_coupling_defaults_to_none() -> None:
    assert _manifest().commit_coupling is None


def test_commit_coupling_accepts_enum_values() -> None:
    for value in ("transactional", "idempotent_keyed", "unconfirmed"):
        assert _manifest(commit_coupling=value).commit_coupling == value


def test_commit_coupling_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        _manifest(commit_coupling="maybe")

"""Story 4.5 AC1 -- "a reversible CommandSpec names an undo command type, and
a tripwire fails a reversible spec without one" (FR33).

``CommandSpec``'s own ``_undo_matches_reversible`` validator (Story 4.3,
``command_spec.py``) already refuses a reversible spec with no
``undo_command_type`` AT CONSTRUCTION -- proven per-instance by
``test_command_spec.py``, not a tripwire. What NOTHING checked before this
story: whether every LIVE, registered command type's declared
``undo_command_type`` actually names something real in
``CommandSpecRegistry`` -- a typo or a forgotten registration would pass the
per-instance validator (a non-empty string) while still being unusable by
``commands/spec/undo.py::request_undo``. This tripwire scans the live
registry for exactly that.
"""

from __future__ import annotations

import pytest

import stackowl.scheduler.commands  # noqa: F401 -- import registers the pilot CommandSpecs
from stackowl.commands.spec.errors import CommandTypeNotDeclaredError
from stackowl.commands.spec.registry import CommandSpecRegistry

pytestmark = pytest.mark.tripwire


def test_every_reversible_command_names_a_registered_undo_type() -> None:
    specs = CommandSpecRegistry.list()
    reversible = [spec for spec in specs if spec.reversible]
    assert reversible, "no reversible CommandSpec is registered — nothing for this tripwire to check"
    for spec in reversible:
        assert spec.undo_command_type, (
            f"{spec.command_type!r} is reversible but declares no "
            "undo_command_type — CommandSpec's own validator should have "
            "already refused this at construction"
        )
        try:
            CommandSpecRegistry.get(spec.undo_command_type)
        except CommandTypeNotDeclaredError:
            pytest.fail(
                f"{spec.command_type!r}'s undo_command_type="
                f"{spec.undo_command_type!r} does not name any registered "
                "CommandSpec"
            )

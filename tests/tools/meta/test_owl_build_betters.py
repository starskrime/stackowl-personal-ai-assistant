"""Unit tests for owl_build's no-edit-your-betters guard (``can_modify``).

``can_modify`` only reads ``.origin`` / ``.created_by`` / ``.name`` so a duck-typed
stand-in is sufficient — no full manifest construction needed.
"""

from stackowl.tools.meta.owl_build import can_modify, can_rename, can_retire


class _Owl:
    def __init__(self, origin, created_by, name="x"):
        self.origin = origin
        self.created_by = created_by
        self.name = name


def test_cannot_edit_secretary():
    assert can_modify(_Owl("builtin", None, "secretary"), caller="secretary", target_name="secretary") is not None


def test_cannot_edit_human_owl():
    assert can_modify(_Owl("human", None, "planner"), caller="secretary", target_name="planner") is not None


def test_cannot_edit_builtin_owl():
    assert can_modify(_Owl("builtin", None, "scribe"), caller="secretary", target_name="scribe") is not None


def test_cannot_edit_another_agents_owl():
    assert can_modify(_Owl("agent", "other_owl", "scout"), caller="secretary", target_name="scout") is not None


def test_can_edit_own_agent_owl():
    assert can_modify(_Owl("agent", "secretary", "scout"), caller="secretary", target_name="scout") is None


def test_can_rename_secretary():
    """Rename is cosmetic-only (display_name) — unlike can_modify, the secretary
    is NOT blocked, since renaming touches no tool/authority/schedule."""
    assert can_rename(_Owl("builtin", None, "secretary"), caller="secretary", target_name="secretary") is None


def test_can_rename_builtin_owl():
    assert can_rename(_Owl("builtin", None, "scribe"), caller="secretary", target_name="scribe") is None


def test_cannot_rename_another_agents_owl():
    assert can_rename(_Owl("agent", "other_owl", "scout"), caller="secretary", target_name="scout") is not None


def test_can_rename_own_agent_owl():
    assert can_rename(_Owl("agent", "secretary", "scout"), caller="secretary", target_name="scout") is None


def test_can_retire_general_builtin():
    """Unlike can_modify, retire allows removing a general-purpose builtin
    (scout/librarian/archivist) — the user explicitly asked for this."""
    assert can_retire(_Owl("builtin", None, "scout"), caller="secretary", target_name="scout") is None


def test_cannot_retire_secretary():
    assert can_retire(_Owl("builtin", None, "secretary"), caller="secretary", target_name="secretary") is not None


def test_cannot_retire_internal_rca_owl():
    assert can_retire(_Owl("builtin", None, "rca_gatherer"), caller="secretary", target_name="rca_gatherer") is not None


def test_cannot_retire_another_agents_owl():
    assert can_retire(_Owl("agent", "other_owl", "scout"), caller="secretary", target_name="scout") is not None


def test_can_retire_own_agent_owl():
    assert can_retire(_Owl("agent", "secretary", "scout"), caller="secretary", target_name="scout") is None

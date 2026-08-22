"""Unit tests for owl_build's no-edit-your-betters guard (``can_modify``).

``can_modify`` only reads ``.origin`` / ``.created_by`` / ``.name`` so a duck-typed
stand-in is sufficient — no full manifest construction needed.
"""

from stackowl.tools.meta.owl_build import can_edit, can_modify, can_rename, can_retire


class _Owl:
    def __init__(self, origin, created_by, name="x"):
        self.origin = origin
        self.created_by = created_by
        self.name = name


def test_cannot_edit_secretary():
    assert can_modify(_Owl("builtin", None, "secretary"), caller="secretary", target_name="secretary") is not None


# BAKIR, 2026-08-22: "Secretary should have access to everything. She is root
# administrator of platform." These three asserted the OPPOSITE with
# caller="secretary" — they encoded the rule the platform's owner has since
# reversed, so they are rewritten rather than deleted: the same protections are
# still pinned, now against a NON-root caller, which is where they belong.
def test_root_may_edit_a_human_owl():
    assert can_modify(_Owl("human", None, "planner"), caller="secretary", target_name="planner") is None


def test_root_may_edit_a_builtin_owl():
    assert can_modify(_Owl("builtin", None, "scribe"), caller="secretary", target_name="scribe") is None


def test_root_may_edit_another_agents_owl():
    """The live refusal: "'syshealth' was created by another owl"."""
    assert can_modify(_Owl("agent", "other_owl", "scout"), caller="secretary", target_name="scout") is None


def test_a_NON_root_owl_still_cannot_edit_a_human_owl():
    assert can_modify(_Owl("human", None, "planner"), caller="mailbutler", target_name="planner") is not None


def test_a_NON_root_owl_still_cannot_edit_a_builtin_owl():
    assert can_modify(_Owl("builtin", None, "scribe"), caller="mailbutler", target_name="scribe") is not None


def test_a_NON_root_owl_still_cannot_edit_another_agents_owl():
    """THE SECURITY LINE. Root is an exemption, not a hole — an agent-minted owl
    must never launder authority through a sibling it does not own."""
    assert can_modify(_Owl("agent", "other_owl", "scout"), caller="mailbutler", target_name="scout") is not None


def test_can_edit_own_agent_owl():
    assert can_modify(_Owl("agent", "secretary", "scout"), caller="secretary", target_name="scout") is None


def test_can_rename_secretary():
    """Rename is cosmetic-only (display_name) — unlike can_modify, the secretary
    is NOT blocked, since renaming touches no tool/authority/schedule."""
    assert can_rename(_Owl("builtin", None, "secretary"), caller="secretary", target_name="secretary") is None


def test_can_rename_builtin_owl():
    assert can_rename(_Owl("builtin", None, "scribe"), caller="secretary", target_name="scribe") is None


def test_root_may_rename_another_agents_owl():
    assert can_rename(_Owl("agent", "other_owl", "scout"), caller="secretary", target_name="scout") is None


def test_a_NON_root_owl_still_cannot_rename_another_agents_owl():
    assert can_rename(_Owl("agent", "other_owl", "scout"), caller="mailbutler", target_name="scout") is not None


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


def test_root_may_retire_another_agents_owl():
    assert can_retire(_Owl("agent", "other_owl", "scout"), caller="secretary", target_name="scout") is None


def test_a_NON_root_owl_still_cannot_retire_another_agents_owl():
    assert can_retire(_Owl("agent", "other_owl", "scout"), caller="mailbutler", target_name="scout") is not None


def test_can_retire_own_agent_owl():
    assert can_retire(_Owl("agent", "secretary", "scout"), caller="secretary", target_name="scout") is None


def test_can_edit_secretary():
    """Edit's own gate is looser than can_modify for builtin/human — preserves
    /owls edit's historical ability to change the Secretary's tier."""
    assert can_edit(_Owl("builtin", None, "secretary"), caller="secretary", target_name="secretary") is None


def test_can_edit_human_owl():
    assert can_edit(_Owl("human", None, "planner"), caller="secretary", target_name="planner") is None


def test_root_may_edit_another_agents_owl_via_can_edit():
    """`can_edit` is the gate `_edit` ACTUALLY calls — this is the one that
    produced the refusal Bakir saw, and it kept passing after `can_modify` was
    exempted, which is how the incomplete fix was caught."""
    assert can_edit(_Owl("agent", "other_owl", "scout"), caller="secretary", target_name="scout") is None


def test_a_NON_root_owl_still_cannot_edit_another_agents_owl_via_can_edit():
    assert can_edit(_Owl("agent", "other_owl", "scout"), caller="mailbutler", target_name="scout") is not None


def test_can_edit_own_agent_owl_via_can_edit():
    assert can_edit(_Owl("agent", "secretary", "scout"), caller="secretary", target_name="scout") is None

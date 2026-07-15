from __future__ import annotations

from stackowl.objectives.model import Objective, Subgoal, SubgoalSpec


def test_objective_epic_fields_default_none() -> None:
    obj = Objective(objective_id="obj-1", owner_id="default", intent="test")
    assert obj.repo is None
    assert obj.integration_branch is None
    assert obj.base_branch is None


def test_objective_epic_fields_settable() -> None:
    obj = Objective(
        objective_id="obj-1", owner_id="default", intent="test",
        repo="/tmp/repo", integration_branch="stackowl/epic-obj-1",
        base_branch="main",
    )
    assert obj.repo == "/tmp/repo"
    assert obj.integration_branch == "stackowl/epic-obj-1"
    assert obj.base_branch == "main"


def test_subgoal_depends_on_defaults_empty() -> None:
    sg = Subgoal(
        subgoal_id="sub-1", owner_id="default", objective_id="obj-1",
        position=0, description="test",
    )
    assert sg.depends_on == []
    assert sg.worktree_path is None
    assert sg.story_branch is None


def test_subgoalspec_depends_on_defaults_empty() -> None:
    spec = SubgoalSpec(description="test")
    assert spec.depends_on == []


def test_subgoalspec_depends_on_settable() -> None:
    spec = SubgoalSpec(description="test", depends_on=[0, 2])
    assert spec.depends_on == [0, 2]

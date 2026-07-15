from __future__ import annotations

from datetime import UTC, datetime

from stackowl.objectives.graph import GraphError, readiness_set, validate_graph
from stackowl.objectives.model import Subgoal, SubgoalSpec

_NOW = datetime.now(tz=UTC)


def _sg(subgoal_id: str, status: str, depends_on: list[str] | None = None) -> Subgoal:
    return Subgoal(
        subgoal_id=subgoal_id, owner_id="default", objective_id="obj-1",
        position=0, description="x", status=status,  # type: ignore[arg-type]
        depends_on=depends_on or [], created_at=_NOW, updated_at=_NOW,
    )


def test_readiness_no_deps_is_ready() -> None:
    subgoals = [_sg("a", "pending")]
    assert readiness_set(subgoals) == {"a"}


def test_readiness_waits_for_incomplete_dep() -> None:
    subgoals = [_sg("a", "running"), _sg("b", "pending", depends_on=["a"])]
    assert readiness_set(subgoals) == set()


def test_readiness_fires_once_dep_done() -> None:
    subgoals = [_sg("a", "done"), _sg("b", "pending", depends_on=["a"])]
    assert readiness_set(subgoals) == {"b"}


def test_readiness_ignores_non_pending() -> None:
    subgoals = [_sg("a", "done"), _sg("b", "done", depends_on=["a"])]
    assert readiness_set(subgoals) == set()


def test_readiness_diamond_all_ready_when_deps_done() -> None:
    subgoals = [
        _sg("a", "done"),
        _sg("b", "done", depends_on=["a"]),
        _sg("c", "done", depends_on=["a"]),
        _sg("d", "pending", depends_on=["b", "c"]),
    ]
    assert readiness_set(subgoals) == {"d"}


def test_validate_graph_accepts_diamond() -> None:
    # D depends on B and C; both depend on A. Legal fan-in, NOT a cycle.
    specs = [
        SubgoalSpec(description="a"),
        SubgoalSpec(description="b", depends_on=[0]),
        SubgoalSpec(description="c", depends_on=[0]),
        SubgoalSpec(description="d", depends_on=[1, 2]),
    ]
    assert validate_graph(specs) is None


def test_validate_graph_detects_self_cycle() -> None:
    specs = [SubgoalSpec(description="a", depends_on=[0])]
    err = validate_graph(specs)
    assert err is not None
    assert err.kind == "cycle"


def test_validate_graph_detects_two_node_cycle() -> None:
    specs = [
        SubgoalSpec(description="a", depends_on=[1]),
        SubgoalSpec(description="b", depends_on=[0]),
    ]
    err = validate_graph(specs)
    assert err is not None
    assert err.kind == "cycle"


def test_validate_graph_detects_out_of_range_index() -> None:
    specs = [SubgoalSpec(description="a", depends_on=[5])]
    err = validate_graph(specs)
    assert err is not None
    assert err.kind == "out_of_range"


def test_validate_graph_accepts_empty_deps() -> None:
    specs = [SubgoalSpec(description="a"), SubgoalSpec(description="b")]
    assert validate_graph(specs) is None

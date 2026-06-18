"""derive_child_task_id determinism (Story D1 §5)."""

from __future__ import annotations

from stackowl.pipeline.durable.delegation_link import derive_child_task_id
from stackowl.pipeline.durable.ledger import idempotency_key


def test_same_delegate_key_gives_same_child_id() -> None:
    dk = idempotency_key("parent-1", 3, "delegate_task", {"goal": "do x"})
    assert derive_child_task_id(dk) == derive_child_task_id(dk)


def test_different_delegate_keys_give_different_child_ids() -> None:
    dk1 = idempotency_key("parent-1", 3, "delegate_task", {"goal": "do x"})
    dk2 = idempotency_key("parent-1", 3, "delegate_task", {"goal": "do y"})
    assert derive_child_task_id(dk1) != derive_child_task_id(dk2)


def test_child_id_is_a_stable_prefixed_string() -> None:
    dk = idempotency_key("parent-1", 0, "delegate_task", {"goal": "g"})
    cid = derive_child_task_id(dk)
    assert cid.startswith("child-")
    assert len(cid) == len("child-") + 32


def test_ancestor_depth_root_is_zero() -> None:
    from stackowl.pipeline.durable.delegation_link import ancestor_depth
    # root has no parent
    assert ancestor_depth("root", lambda _tid: None) == 0


def test_ancestor_depth_child_is_one() -> None:
    from stackowl.pipeline.durable.delegation_link import ancestor_depth
    parents = {"child": "root", "root": None}
    assert ancestor_depth("child", parents.get) == 1


def test_ancestor_depth_grandchild_is_two() -> None:
    from stackowl.pipeline.durable.delegation_link import ancestor_depth
    parents = {"grand": "child", "child": "root", "root": None}
    assert ancestor_depth("grand", parents.get) == 2


def test_ancestor_depth_breaks_on_cycle_defensively() -> None:
    from stackowl.pipeline.durable.delegation_link import ancestor_depth
    # A pathological self-cycle must not loop forever — it is bounded.
    parents = {"a": "b", "b": "a"}
    depth = ancestor_depth("a", parents.get)
    assert isinstance(depth, int)
    assert depth >= 0

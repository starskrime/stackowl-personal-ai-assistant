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

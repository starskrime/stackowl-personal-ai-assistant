"""delegation_link — pure helpers tying a durable parent to its durable children.

* :func:`derive_child_task_id` — the child task id is a deterministic function of
  the parent's own resume-stable ``delegate_task`` ledger idempotency key
  (``delegate_key``). A re-sampled parent that re-emits the same delegation at the
  same iteration with the same args computes the same ``delegate_key`` (via
  :func:`stackowl.pipeline.durable.ledger.idempotency_key`) → the same
  ``child_task_id`` → it re-attaches to the existing child row instead of forking
  (D1 §5). This inherits the base ledger's exactly-once semantics verbatim.
* :func:`ancestor_depth` — reconstructs a task's delegation depth by walking the
  ``parent_task_id`` chain via a pure lookup, so a resumed interior node keeps its
  true depth instead of restarting the counter at 0 (D1 §9).
"""

from __future__ import annotations

from collections.abc import Callable

from stackowl.infra.observability import log

#: Length of the delegate_key prefix folded into the child id (sha256 hexdigest
#: is 64 chars; 32 keeps the id short while remaining collision-free across
#: distinct delegate_keys for the table sizes D1 targets).
_CHILD_ID_KEY_PREFIX = 32


def derive_child_task_id(delegate_key: str) -> str:
    """Return the deterministic child task id for a parent ``delegate_key``.

    Pure: the same ``delegate_key`` always yields the same id; distinct keys
    yield distinct ids (the prefix is taken from a sha256 hexdigest, so a
    32-char prefix is collision-free for distinct keys at the scales D1 targets).
    """
    child_id = f"child-{delegate_key[:_CHILD_ID_KEY_PREFIX]}"
    log.tasks.debug(
        "[tasks] delegation_link.derive_child_task_id",
        extra={"_fields": {"delegate_key_prefix": delegate_key[:8], "child_id": child_id}},
    )
    return child_id


#: Hard ceiling on the ancestor walk so a malformed parent chain (a cycle from a
#: corrupted row) can never spin forever. Far above MAX_DELEGATION_DEPTH=2.
_MAX_ANCESTOR_WALK = 64


def ancestor_depth(
    task_id: str,
    parent_of: Callable[[str], str | None],
) -> int:
    """Count delegation ancestors of ``task_id`` via the parent chain.

    ``parent_of(tid)`` returns the ``parent_task_id`` of ``tid`` (or ``None`` for
    a root / unknown id). A root returns 0, its child 1, a grandchild 2. Pure:
    the caller supplies the lookup so this never touches the DB. Bounded by
    :data:`_MAX_ANCESTOR_WALK` and a visited-set so a corrupted cyclic chain
    terminates loudly instead of looping.
    """
    depth = 0
    seen: set[str] = {task_id}
    current = parent_of(task_id)
    while current is not None and depth < _MAX_ANCESTOR_WALK:
        depth += 1
        if current in seen:
            log.tasks.error(
                "[tasks] delegation_link.ancestor_depth: cycle in parent chain — stopping",
                extra={"_fields": {"task_id": task_id, "cycle_at": current, "depth": depth}},
            )
            break
        seen.add(current)
        current = parent_of(current)
    log.tasks.debug(
        "[tasks] delegation_link.ancestor_depth: exit",
        extra={"_fields": {"task_id": task_id, "depth": depth}},
    )
    return depth

"""delegation_link — pure helpers tying a durable parent to its durable children.

* :func:`derive_child_task_id` — the child task id is a deterministic function of
  the parent's own resume-stable ``delegate_task`` ledger idempotency key
  (``delegate_key``). A re-sampled parent that re-emits the same delegation at the
  same iteration with the same args computes the same ``delegate_key`` (via
  :func:`stackowl.pipeline.durable.ledger.idempotency_key`) → the same
  ``child_task_id`` → it re-attaches to the existing child row instead of forking
  (D1 §5). This inherits the base ledger's exactly-once semantics verbatim.
"""

from __future__ import annotations

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

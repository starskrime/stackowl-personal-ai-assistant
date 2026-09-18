"""``compute_registry_digest`` -- a stable fingerprint of the registered journal
event types (Spec 2.3, gateway/core Hello exchange).

Pure, synchronous, in-memory-only -- no DB/network I/O, mirroring
``attention.classify``'s purity (safe to call from a live connection-accept
path with no await). Folds in :data:`~stackowl.journal.attention.ATTENTION_POLICY_VERSION`
alongside every ``(type, schema_version)`` pair so a gateway/core split whose
attention-classification CODE disagrees -- not just their declared types -- is
still caught by ``runtime.hello.evaluate_hello``.
"""

from __future__ import annotations

import hashlib

from stackowl.infra.observability import log
from stackowl.journal.attention import ATTENTION_POLICY_VERSION
from stackowl.journal.registry import get_registry


def compute_registry_digest() -> str:
    """A short, deterministic digest of every registered event type + its
    schema version + the attention-policy version.

    Sorted so registration ORDER never changes the digest -- only the actual
    declared set does. First 16 hex chars of a sha256 over the canonical
    string; short enough to log/compare by eye, long enough that an accidental
    collision between two genuinely different registries is not a live
    concern for this story's purpose (a fast, cheap equality check, not a
    cryptographic guarantee).
    """
    # 1. ENTRY -- DEBUG only: called on every accepted connection (gateway)
    # and every Hello build (both sides), a hot-ish path (mirrors
    # attention.classify's own DEBUG entry/exit on its own hot path).
    log.journal.debug("[journal] digest.compute_registry_digest: entry")
    # 2. STEP -- one registry read; no branching of its own.
    registry = get_registry()
    pairs = sorted(
        (type_name, registry.get(type_name).schema_version)
        for type_name in registry.all_types()
    )
    canonical = (
        "|".join(f"{type_name}:{schema_version}" for type_name, schema_version in pairs)
        + f"|attention_policy:{ATTENTION_POLICY_VERSION}"
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    # 4. EXIT
    log.journal.debug(
        "[journal] digest.compute_registry_digest: exit",
        extra={"_fields": {"type_count": len(pairs), "digest": digest}},
    )
    return digest

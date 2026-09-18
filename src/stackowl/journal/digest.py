"""``compute_registry_digest`` -- a stable fingerprint of the registered journal
event types (Spec 2.3, gateway/core Hello exchange).

Pure, synchronous, in-memory-only -- no DB/network I/O, mirroring
``attention.classify``'s purity (safe to call from a live connection-accept
path with no await). Folds in :data:`~stackowl.journal.attention.ATTENTION_POLICY_VERSION`
alongside every ``(type, schema_version)`` pair so a gateway/core split whose
attention-classification CODE disagrees -- not just their declared types -- is
still caught by ``runtime.hello.evaluate_hello``.

Story 3.1 -- also folds in each spec's ``needs_you_kind``/``resolves``.
AD-3: "for types that open Needs-you items, its resolving types" is part of
what the registry declares per type. Without this, a gateway/core pair that
agrees on every ``(type, schema_version)`` but disagrees on
``needs_you_kind``/``resolves`` (e.g. one side missed bumping
``ATTENTION_POLICY_VERSION`` by hand after changing one) would silently pass
the Hello compatibility check while the two sides actually disagree about
whether/how an event opens or closes a Needs-you item.
"""

from __future__ import annotations

import hashlib

from stackowl.infra.observability import log
from stackowl.journal.attention import ATTENTION_POLICY_VERSION
from stackowl.journal.registry import get_registry


def compute_registry_digest() -> str:
    """A short, deterministic digest of every registered event type + its
    schema version + its ``needs_you_kind``/``resolves`` + the
    attention-policy version.

    Sorted so registration ORDER never changes the digest -- only the actual
    declared set does (``resolves`` is itself sorted per type for the same
    reason: two functionally-identical tuples in a different order must not
    digest differently). First 16 hex chars of a sha256 over the canonical
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
    rows = []
    for type_name in registry.all_types():
        spec = registry.get(type_name)
        needs_you_kind = spec.needs_you_kind.value if spec.needs_you_kind is not None else ""
        resolves = ",".join(sorted(spec.resolves))
        rows.append((type_name, spec.schema_version, needs_you_kind, resolves))
    rows.sort()
    canonical = (
        "|".join(
            f"{type_name}:{schema_version}:{needs_you_kind}:{resolves}"
            for type_name, schema_version, needs_you_kind, resolves in rows
        )
        + f"|attention_policy:{ATTENTION_POLICY_VERSION}"
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    # 4. EXIT
    log.journal.debug(
        "[journal] digest.compute_registry_digest: exit",
        extra={"_fields": {"type_count": len(rows), "digest": digest}},
    )
    return digest

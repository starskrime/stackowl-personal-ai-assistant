"""Semantic near-match: refuse a create that duplicates an existing owl; redirect to
delegate. Fails OPEN (no semantic embedder -> None; name-equality is already covered by
name-quality). A duplicate role is a delegation opportunity, not a new owl."""
from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.memory.sqlite_helpers import cosine_similarity
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.tools.meta.owl_build_spec import OwlBuildSpec

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.pipeline.services import StepServices

_SIMILARITY_THRESHOLD = 0.85


def _normalize_name_tokens(name: str) -> set[str]:
    """Lowercase, split on non-alphanumeric, drop empty tokens.

    'research_brain' -> {'research', 'brain'}; 'Researcher Brain' -> {'researcher', 'brain'};
    'Brain' -> {'brain'}. Used for a cheap, deterministic near-duplicate check that
    works even with no semantic embedder wired (today's fail-open path has ZERO
    duplicate protection beyond exact name-equality in that mode)."""
    import re
    return {t for t in re.split(r"[^a-z0-9]+", name.lower()) if t}


def _name_token_overlap_match(spec_name: str, others: list[OwlAgentManifest]) -> str | None:
    """Return an existing owl's name if it shares a token with ``spec_name``, else None.

    Deliberately cheap and deterministic (no embedding call) — catches the exact
    incident shape confirmed live: 'research_brain' / 'Researcher Brain' both
    share the 'brain' token with an existing 'Brain' owl, even though their
    GENERATED SPECIALTY TEXT doesn't score high enough cosine similarity to trip
    the semantic check below. A single shared token is deliberately a LOW bar —
    this is a create-time refusal, not a silent auto-merge; a false positive just
    means the model gets redirected to delegate_task/edit and can still proceed
    under a genuinely different name if it disagrees."""
    spec_tokens = _normalize_name_tokens(spec_name)
    if not spec_tokens:
        return None
    for m in others:
        if _normalize_name_tokens(m.name) & spec_tokens:
            return m.name
    return None


async def existing_near_match(
    spec: OwlBuildSpec, registry: OwlRegistry, services: StepServices
) -> str | None:
    """Return the name of a near-identical existing owl, or None.

    Fail-open: no embedding registry, a non-semantic (hash) fallback provider, no
    peers, or any embed error -> None (name-equality still guards exact dupes)."""
    others = list(registry.all())
    if not others:
        return None
    token_match = _name_token_overlap_match(spec.name, others)
    if token_match is not None:
        log.tool.info(
            "owl_build.existence: name-token overlap found — redirecting to delegate",
            extra={"_fields": {"owl": spec.name, "match": token_match}},
        )
        return token_match
    reg = getattr(services, "embedding_registry", None)
    if reg is None:
        return None  # fail-open — no embedder wired
    # Hash-fallback cosine is meaningless; only trust a genuine semantic model.
    if not getattr(reg, "is_semantic", False):
        log.tool.info(
            "owl_build.existence: non-semantic embedder — skipping near-match (fail open)",
            extra={"_fields": {"owl": spec.name}},
        )
        return None
    query = f"{spec.name} {spec.specialty or ''}".strip()
    try:
        provider = reg.get()
        texts = [query] + [f"{m.name} {m.role}" for m in others]
        vectors = await provider.embed(texts)
    except Exception as exc:  # no-hidden-errors — log + fail open
        log.tool.error(
            "owl_build.existence: embed failed, failing open",
            exc_info=exc,
            extra={"_fields": {"owl": spec.name}},
        )
        return None
    q = vectors[0]
    best_name: str | None = None
    best_score = -1.0
    for m, vec in zip(others, vectors[1:], strict=True):
        score = cosine_similarity(q, vec)
        if score is not None and score > best_score:
            best_name, best_score = m.name, score
    if best_name is not None and best_score >= _SIMILARITY_THRESHOLD:
        log.tool.info(
            "owl_build.existence: near-duplicate found — redirecting to delegate",
            extra={"_fields": {"owl": spec.name, "match": best_name, "score": best_score}},
        )
        return best_name
    return None

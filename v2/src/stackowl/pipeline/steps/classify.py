"""Pipeline step 3: classify — populate memory_context via MemoryBridge.

When a KuzuAdapter is wired into :class:`StepServices`, the step appends
best-effort graph context after the FTS5/LanceDB recall: any KuzuAdapter
failure falls back silently with a warning log so the pipeline never
crashes on a graph hiccup.
"""

from __future__ import annotations

import hashlib
import re

from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState

# Unicode tokenisation — stdlib ``re`` ``\w`` already covers \p{L}\p{N}_
# under the UNICODE flag, which is enabled by default on Python 3. Never
# hardcode an English-only stopword list here.
_WORD_RE = re.compile(r"\w{3,}", re.UNICODE)


def _candidate_entity_ids(query: str, limit: int = 5) -> list[str]:
    """Derive deterministic entity ids from query tokens.

    Mirrors :func:`stackowl.memory.kuzu_sync_handler._entity_id_for` so the
    traversal can look up entities mirrored from committed facts. The
    handler writes ``entity_type|name`` digests; here we try a small set
    of common entity types because the pipeline does not know in advance
    which type a query token belongs to.
    """
    tokens = _WORD_RE.findall(query)
    if not tokens:
        return []
    candidate_types = ("PERSON", "ORG", "TOPIC", "CONCEPT", "LOCATION", "OTHER")
    ids: list[str] = []
    seen: set[str] = set()
    for token in tokens[:limit]:
        for ent_type in candidate_types:
            digest = hashlib.sha256(
                f"{ent_type}|{token}".encode("utf-8")
            ).hexdigest()[:16]
            entity_id = f"ent_{digest}"
            if entity_id in seen:
                continue
            seen.add(entity_id)
            ids.append(entity_id)
    return ids


async def _gather_graph_context(query: str) -> str:
    """Best-effort Kuzu traversal. Returns appended context or ``""``."""
    services = get_services()
    adapter = services.kuzu_adapter
    if adapter is None:
        return ""
    candidates = _candidate_entity_ids(query)
    if not candidates:
        return ""
    collected: list[str] = []
    for entity_id in candidates:
        try:
            rows = await adapter.traverse(entity_id, max_hops=1)
        except Exception as exc:
            # B5 — never crash classify on a graph hiccup
            log.engine.warning(
                "[pipeline] classify: kuzu traverse failed — skipping",
                exc_info=exc,
                extra={"_fields": {"entity_id": entity_id}},
            )
            continue
        for row in rows:
            name = row.get("name") or ""
            ent_type = row.get("entity_type") or ""
            if not name:
                continue
            collected.append(f"- {name} ({ent_type})")
    if not collected:
        return ""
    lines = ["Related entities:"]
    lines.extend(collected[:10])
    return "\n".join(lines)


async def run(state: PipelineState) -> PipelineState:
    log.engine.debug(
        "[pipeline] classify: entry", extra={"_fields": {"trace_id": state.trace_id}}
    )
    services = get_services()
    bridge = services.memory_bridge
    if bridge is None:
        log.engine.debug("[pipeline] classify: no memory_bridge — pass-through")
        return state
    context = await bridge.retrieve(state.input_text, state.session_id)
    graph_context = await _gather_graph_context(state.input_text)
    if graph_context:
        context = f"{context}\n\n{graph_context}" if context else graph_context
    log.engine.debug(
        "[pipeline] classify: exit",
        extra={
            "_fields": {
                "trace_id": state.trace_id,
                "context_len": len(context),
                "graph_context_len": len(graph_context),
            }
        },
    )
    return state.evolve(memory_context=context or None)

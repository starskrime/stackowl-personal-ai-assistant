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
from stackowl.learning.heuristic_ranking import rank_lessons
from stackowl.pipeline import lesson_context as lc
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import TOOL_FREE_CLASSES, PipelineState
from stackowl.providers.base import Message

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
                f"{ent_type}|{token}".encode()
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


async def _gather_preferences(session_id: str) -> str:
    """Best-effort: load persisted preferences for the owner and format for the prompt.

    Failures (store missing, DB error) return ``""`` — preferences are
    enhancement, not gating. Never blocks the pipeline.
    """
    services = get_services()
    store = services.preference_store
    if store is None:
        return ""
    try:
        # owner_key currently maps to session_id; will become channel-prefixed
        # when per-channel threading lands. Both paths read from the same store.
        prefs = await store.list_for_owner(session_id)
    except Exception as exc:
        log.engine.warning(
            "[pipeline] classify: preference load failed — skipping",
            exc_info=exc, extra={"_fields": {"session_id": session_id}},
        )
        return ""
    if not prefs:
        return ""
    lines = ["## Learned Preferences"]
    lines.extend(f"- {k}: {v}" for k, v in sorted(prefs.items()))
    return "\n".join(lines)


def _should_surface_failure_history(state: PipelineState) -> bool:
    """Fail-closed admission gate for failure-history prompt blocks.

    Past-failure context (``## Recent Reflections`` / ``## What You Did
    Recently``) is admitted ONLY on a turn the router POSITIVELY classified as
    standard/work. A greeting that lands as ``standard`` by default — the
    direct-address bypass, or any router error — is NOT a confirmed work turn,
    so its failure history is withheld. Trade chosen deliberately: omitting a
    reflection on a true work turn is mild degradation; injecting phantom
    failure-history into a greeting is trust-destroying. Fail toward silence.
    """
    return state.intent_class == "standard" and state.intent_classified


async def _gather_recent_reflections(owl_name: str, limit: int = 3) -> str:
    """Best-effort: surface the agent's most recent reflections for this owl.

    Reflexion-style — what went wrong recently and what the agent suggested
    doing differently. Reads from the ``reflections`` table (Commit 2). When
    the table is empty or the DB pool isn't wired (tests, dry-run), returns
    "" and the rest of classify proceeds normally.

    Note: this is a recency-based fallback. Semantic recall over the
    embedding column lands in Commit 5 (lessons_index) which lets us surface
    reflections matching the CURRENT query's failure pattern, not just the
    most-recent-N.
    """
    # 1. ENTRY
    log.engine.debug(
        "[pipeline] classify._gather_recent_reflections: entry",
        extra={"_fields": {"owl_name": owl_name, "limit": limit}},
    )
    services = get_services()
    db = services.db_pool
    # 2. DECISION — db not wired (tests / dry-run)
    if db is None or limit <= 0:
        log.engine.debug(
            "[pipeline] classify._gather_recent_reflections: exit — no db_pool",
        )
        return ""
    # 3. STEP — pull recent reflections
    try:
        from stackowl.memory.reflection_store import ReflectionStore

        store = ReflectionStore(db)
        reflections = await store.recent_for_owl(owl_name, limit=limit)
    except Exception as exc:  # B5
        log.engine.warning(
            "[pipeline] classify._gather_recent_reflections: lookup failed — skipping",
            exc_info=exc, extra={"_fields": {"owl_name": owl_name}},
        )
        return ""
    # 2. DECISION — nothing to surface
    if not reflections:
        log.engine.debug(
            "[pipeline] classify._gather_recent_reflections: exit — no reflections",
            extra={"_fields": {"owl_name": owl_name}},
        )
        return ""
    lines = ["## Recent Reflections"]
    for r in reflections:
        tag = f" [{r.failure_class}]" if r.failure_class else ""
        lines.append(f"- {r.summary}{tag}")
        if r.suggested_strategy:
            lines.append(f"  → strategy: {r.suggested_strategy}")
    result = "\n".join(lines)
    # 4. EXIT
    log.engine.debug(
        "[pipeline] classify._gather_recent_reflections: exit",
        extra={"_fields": {
            "owl_name": owl_name, "n_reflections": len(reflections),
            "block_len": len(result),
        }},
    )
    return result


async def _gather_recent_actions(
    session_id: str, trace_id: str, limit: int = 3,
) -> str:
    """Best-effort: surface what the agent DID on recent turns this session.

    Live action recall — lets the agent answer "what did you just do?" by
    reading back the task_outcomes (tool_sequence + response + success) it
    captured on prior turns of the SAME session. ``trace_id`` is the in-flight
    turn and is excluded so the agent never echoes the current question.

    Returns "" when db_pool isn't wired (tests/dry-run), on any logged error,
    or when there are no prior outcomes — the rest of classify proceeds.
    """
    # 1. ENTRY
    log.engine.debug(
        "[pipeline] classify._gather_recent_actions: entry",
        extra={"_fields": {"session_id": session_id, "limit": limit}},
    )
    services = get_services()
    db = services.db_pool
    # 2. DECISION — db not wired (tests / dry-run) or nothing requested
    if db is None or limit <= 0:
        log.engine.debug(
            "[pipeline] classify._gather_recent_actions: exit — no db_pool",
        )
        return ""
    # 3. STEP — pull recent outcomes for this session (excluding in-flight)
    try:
        from stackowl.memory.outcome_store import TaskOutcomeStore

        store = TaskOutcomeStore(db)
        outcomes = await store.recent_for_session(
            session_id, limit=limit, exclude_trace_id=trace_id,
        )
    except Exception as exc:  # B5 — never crash classify on a recall hiccup
        log.engine.warning(
            "[pipeline] classify._gather_recent_actions: lookup failed — skipping",
            exc_info=exc, extra={"_fields": {"session_id": session_id}},
        )
        return ""
    # 2. DECISION — nothing to surface
    if not outcomes:
        log.engine.debug(
            "[pipeline] classify._gather_recent_actions: exit — no outcomes",
            extra={"_fields": {"session_id": session_id}},
        )
        return ""
    # 4. EXIT — fixed ascii header; user content sliced by codepoint (never tokenised)
    lines = ["## What You Did Recently"]
    for o in outcomes:
        glyph = "✔" if o.success else "✘"
        tools = ", ".join(o.tool_sequence) if o.tool_sequence else "(none)"
        tag = f" [{o.failure_class}]" if o.failure_class else ""
        lines.append(
            f"- {glyph} {o.input_text[:100]} | tools: {tools}{tag}"
            f" -> {o.response_text[:120]}",
        )
    result = "\n".join(lines)
    log.engine.debug(
        "[pipeline] classify._gather_recent_actions: exit",
        extra={"_fields": {
            "session_id": session_id, "n_outcomes": len(outcomes),
            "block_len": len(result),
        }},
    )
    return result


async def _gather_relevant_skills(
    query: str,
    limit: int = 3,
    owned: set[str] | None = None,
    pre_embedded: tuple[float, ...] | None = None,
) -> str:
    """Best-effort: surface up to K skills semantically relevant to ``query``.

    Per the Commit 3 sub-phase 3d operator vote:
    * K=3 surfaced max (token-bloat ceiling)
    * Description + when_to_use only — body NOT included (agent can read full
      playbook via ``/skill show <name>`` when it wants it)

    Returns ``""`` when skill_store or embedding_registry isn't wired (tests,
    dry-run, early boot before SkillsAssembly is built) or when nothing scores
    above the threshold. The rest of classify proceeds normally.
    """
    # 1. ENTRY
    log.engine.debug(
        "[pipeline] classify._gather_relevant_skills: entry",
        extra={"_fields": {"query_len": len(query), "limit": limit}},
    )
    services = get_services()
    skill_store = services.skill_store
    embedding_registry = services.embedding_registry
    # 2. DECISION — wires absent (tests / dry-run)
    if skill_store is None or embedding_registry is None or limit <= 0:
        log.engine.debug(
            "[pipeline] classify._gather_relevant_skills: exit — wires absent",
        )
        return ""
    # 3. STEP — embed the user query (reuse a pre-embedded vector when the caller
    # already computed it this turn, avoiding a redundant embedding of the same text)
    if pre_embedded is not None:
        vectors: list[list[float]] = [list(pre_embedded)]
    else:
        try:
            vectors = await embedding_registry.get().embed([query])
        except Exception as exc:  # B5
            log.engine.warning(
                "[pipeline] classify._gather_relevant_skills: embed failed — skipping",
                exc_info=exc, extra={"_fields": {"query_len": len(query)}},
            )
            return ""
    if not vectors or not vectors[0]:
        log.engine.debug(
            "[pipeline] classify._gather_relevant_skills: exit — empty embedding",
        )
        return ""
    # 3. STEP — semantic recall over the SQLite skills index
    try:
        hits = await skill_store.semantic_recall(list(vectors[0]), limit=limit)
    except Exception as exc:  # B5
        log.engine.warning(
            "[pipeline] classify._gather_relevant_skills: recall failed — skipping",
            exc_info=exc,
        )
        return ""
    if not hits:
        log.engine.debug(
            "[pipeline] classify._gather_relevant_skills: exit — no matches",
        )
        return ""
    # Filter owned skills so they don't appear at two altitudes (owned-playbook
    # section AND relevant-skills block → weak-model repetition loops).
    if owned:
        hits = [(sk, sim) for sk, sim in hits if sk.name not in owned]
    if not hits:
        log.engine.debug(
            "[pipeline] classify._gather_relevant_skills: exit — all hits owned",
        )
        return ""
    # 4. EXIT — format the prompt block
    lines = ["## Relevant Skills"]
    for sk, sim in hits:
        desc = sk.description[:160]
        line = f"- **{sk.name}** ({sim:.2f}): {desc}"
        if sk.when_to_use:
            line += f" — _{sk.when_to_use[:160]}_"
        lines.append(line)
    lines.append("(Use `/skill show <name>` for the full playbook.)")
    result = "\n".join(lines)
    log.engine.debug(
        "[pipeline] classify._gather_relevant_skills: exit",
        extra={"_fields": {
            "n_hits": len(hits), "block_len": len(result),
            "top_sim": hits[0][1],
        }},
    )
    return result


async def _gather_lessons(query: str, limit: int = 3) -> str:
    """Best-effort: surface up to K cross-source lessons (Learning Commit 5).

    Queries the unified LessonsIndex (LanceDB) which holds reflections + tool
    heuristics + skills + pellets in one table. Returns the matched lessons
    grouped by source_type so the LLM sees the relevant slice of each.
    Distinct from ``_gather_relevant_skills`` which retrieves ONLY skills
    via SQLite cosine — this surfaces reflections+heuristics+pellets that
    don't have a SQLite recall path of their own.
    """
    # 1. ENTRY
    log.engine.debug(
        "[pipeline] classify._gather_lessons: entry",
        extra={"_fields": {"query_len": len(query), "limit": limit}},
    )
    services = get_services()
    lessons_index = services.lessons_index
    if lessons_index is None or limit <= 0:
        log.engine.debug(
            "[pipeline] classify._gather_lessons: exit — no lessons_index wired",
        )
        return ""
    # 3. STEP — single ANN query across all source_types
    try:
        hits = await lessons_index.search(query, limit=limit)
    except Exception as exc:  # B5
        log.engine.warning(
            "[pipeline] classify._gather_lessons: lessons.search failed — skipping",
            exc_info=exc,
        )
        return ""
    # Filter out skill source — those already come through _gather_relevant_skills.
    # Lessons surface adds the OTHER sources (reflections/heuristics/pellets).
    non_skill_hits = [h for h in hits if h.source_type != "skill"]
    if not non_skill_hits:
        log.engine.debug(
            "[pipeline] classify._gather_lessons: exit — only-skill or no hits",
        )
        return ""
    # 4. EXIT — rank heuristics by confidence, assign turn-local ids, stash + format.
    ranked = rank_lessons(non_skill_hits)
    surfaced: list[lc.SurfacedLesson] = []
    lines = [
        "## Cross-Source Lessons",
        "If a lesson below changed what you did, call note_applied_lesson with its id.",
    ]
    for i, h in enumerate(ranked, start=1):
        lid = f"L{i}"
        snippet = h.content[:300]
        lines.append(f"- [{lid}] **[{h.source_type}]** ({h.similarity:.2f}) {snippet}")
        surfaced.append(lc.SurfacedLesson(
            lesson_id=lid, source_type=h.source_type, content=h.content, similarity=h.similarity,
        ))
    lc.set_surfaced(tuple(surfaced))
    result = "\n".join(lines)
    log.engine.debug(
        "[pipeline] classify._gather_lessons: exit",
        extra={"_fields": {
            "n_hits": len(ranked),
            "block_len": len(result),
            "top_sim": ranked[0].similarity if ranked else None,
        }},
    )
    return result


def _parse_turns_to_messages(contents: list[str]) -> list[Message]:
    """Parse stored "User: X\n\nAssistant: Y" rows into real Message turns.

    The store format is fixed by consolidate.py: f"User: {input}\n\nAssistant: {reply}".
    Returns oldest-first user/assistant pairs; skips empty halves so we never
    emit a blank-content turn (providers reject empty content).
    """
    msgs: list[Message] = []
    for content in contents:
        user_part, _, assistant_part = content.partition("\n\nAssistant:")
        user_text = user_part.removeprefix("User:").strip()
        assistant_text = assistant_part.strip()
        if user_text:
            msgs.append(Message(role="user", content=user_text))
        if assistant_text:
            msgs.append(Message(role="assistant", content=assistant_text))
    return msgs


def _dedup_assistant_history(messages: list[Message]) -> list[Message]:
    """Collapse repeated assistant turns to their most-recent occurrence.

    A weak model that re-sends the same correction/apology turn after turn gets
    that prose persisted and re-fed (window 6), which reinforces the loop. We
    keep USER turns verbatim (the real conversation) and, for assistant turns,
    drop every earlier occurrence of a content that recurs later in the window
    — deterministic, content-keyed, no natural-language apology detection.
    """
    seen_later: dict[str, int] = {}
    for i, m in enumerate(messages):
        if m.role == "assistant":
            seen_later[m.content.strip()] = i  # last index wins
    out: list[Message] = []
    for i, m in enumerate(messages):
        if m.role == "assistant" and seen_later.get(m.content.strip(), i) != i:
            continue  # an identical assistant turn appears later — drop this earlier one
        out.append(m)
    return out


async def _gather_history(session_id: str, limit: int) -> list[Message]:
    """Fetch the last ``limit`` staged conversation turns as real Message objects.

    Returns oldest-first user/assistant Message pairs for direct injection into
    the model's message history (not folded into the system-prompt text block).
    """
    services = get_services()
    bridge = services.memory_bridge
    if bridge is None or limit <= 0:
        return []
    try:
        turns = await bridge.recent_conversation_turns(session_id=session_id, limit=limit)
    except Exception as exc:
        log.engine.error(
            "[pipeline] classify: history fetch FAILED — short-term memory degraded",
            exc_info=exc, extra={"_fields": {"session_id": session_id}},
        )
        return []
    return _dedup_assistant_history(_parse_turns_to_messages([t.content for t in turns]))


async def run(state: PipelineState) -> PipelineState:
    log.engine.debug(
        "[pipeline] classify: entry", extra={"_fields": {"trace_id": state.trace_id}}
    )
    services = get_services()
    bridge = services.memory_bridge
    if bridge is None:
        log.engine.debug("[pipeline] classify: no memory_bridge — pass-through")
        return state
    # Long-term committed-fact context (FTS or semantic).
    context = await bridge.retrieve(state.input_text, state.session_id)
    # Short-term: last N turns of the current session.
    try:
        from stackowl.config.settings import Settings

        short_term_window = Settings().memory.short_term_window
    except Exception:
        short_term_window = 6
    history = await _gather_history(state.session_id, short_term_window)
    # Lean gate: conversational turns (greetings/small-talk) skip every heavy
    # block that would only balloon the prompt for no task-relevant gain.
    # "standard" (the default) is byte-identical to prior behavior.
    _lean = state.intent_class in TOOL_FREE_CLASSES
    if _lean:
        log.engine.info(
            "[pipeline] classify: tool-free class — lean assembly (skipping heavy blocks)",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
    # Long-term graph context.
    graph_context = "" if _lean else await _gather_graph_context(state.input_text)
    # Persisted user preferences (high priority — pin to top, always included).
    prefs_block = await _gather_preferences(state.session_id)
    # Reflexion-style learnings from past failures (Commit 2).
    _surface_failures = _should_surface_failure_history(state)
    reflections_block = await _gather_recent_reflections(state.owl_name, limit=3) if _surface_failures else ""
    # Live action recall — what the agent DID on prior turns this session
    # (excludes the in-flight turn). Lets it answer "what did you just do?".
    actions_block = await _gather_recent_actions(
        state.session_id, state.trace_id, limit=3,
    ) if _surface_failures else ""
    # Voyager-style skills relevant to this query (Commit 3 sub-phase 3d).
    # Suppress owned skills so they don't appear at two altitudes.
    # Owned-skill lookup is only used by _gather_relevant_skills — skip on lean.
    owned: set[str] = set()
    if not _lean:
        _reg = get_services().owl_registry
        if _reg is not None:
            try:
                owned = set(_reg.get(state.owl_name).skills)
            except Exception as exc:  # unknown owl / lookup failure → no suppression (safe)
                log.engine.debug(
                    "[pipeline] classify: owned-skill lookup failed — no suppression",
                    exc_info=exc, extra={"_fields": {"owl": state.owl_name}},
                )
                owned = set()
    # Compute the query embedding once (semantic-guarded) and stash on state so the
    # assemble step can score owned skills without re-embedding. Story B.
    # The same vector is threaded into _gather_relevant_skills (pre_embedded) so the
    # query is embedded once per turn instead of twice (F065).
    # The embedding is NOT gated on _lean: assemble uses it for owned-skill tiering
    # independently of the heavy gather blocks.
    query_embedding: tuple[float, ...] | None = None
    emb_reg = get_services().embedding_registry
    if emb_reg is not None and getattr(emb_reg, "is_semantic", False) and state.input_text.strip():
        try:
            vecs = await emb_reg.get().embed([state.input_text])
            if vecs and vecs[0]:
                query_embedding = tuple(float(x) for x in vecs[0])
        except Exception as exc:  # no-hidden-errors: degrade to no-relevance (fallback)
            log.engine.error(
                "[pipeline] classify: query embed failed — skill tiering will fall back",
                exc_info=exc,
                extra={"_fields": {"owl": state.owl_name}},
            )
    skills_block = (
        ""
        if _lean
        else await _gather_relevant_skills(
            state.input_text, limit=3, owned=owned, pre_embedded=query_embedding
        )
    )
    # Cross-source lessons (Learning Commit 5) — reflections/tool heuristics/
    # pellets from the unified LanceDB lessons index.
    lessons_block = "" if _lean else await _gather_lessons(state.input_text, limit=3)
    # Combine: prefs first (always in view), then skills (what tactics apply),
    # then lessons (cross-source learnings), then reflections (what went wrong
    # before), then long-term context, then graph.
    # NOTE: prior conversation turns are NO LONGER included here — they are
    # passed as real message history via state.history to avoid duplication.
    parts = [
        p for p in (
            prefs_block, skills_block, lessons_block, reflections_block,
            actions_block, context, graph_context,
        ) if p
    ]
    combined = "\n\n".join(parts)
    log.engine.debug(
        "[pipeline] classify: exit",
        extra={
            "_fields": {
                "trace_id": state.trace_id,
                "context_len": len(combined),
                "prefs_len": len(prefs_block),
                "skills_len": len(skills_block),
                "lessons_len": len(lessons_block),
                "reflections_len": len(reflections_block),
                "actions_len": len(actions_block),
                "history_len": len(history),
                "graph_context_len": len(graph_context),
            }
        },
    )
    return state.evolve(memory_context=combined or None, history=tuple(history), query_embedding=query_embedding)

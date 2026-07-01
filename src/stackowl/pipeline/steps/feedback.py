"""Pipeline step: feedback — capture a reaction to the LAST render into the
durable ``output_style`` preference, aspect-scoped (LS4).

When the user reacts to the assistant's previous message ("I like this, keep it"
/ "no, you broke it / I still see asterisks / no links"), this step turns that
reaction into an ENFORCED preference so the NEXT render obeys (LS2 enforces it at
the delivery seam). It is the CAPTURE half of ADR-S2's bidirectional learning;
LS3's :class:`FeedbackClassifier` is the verdict, LS2's :class:`OutputStyle` is
the enforcement. This step only WRITES the preference (+ an outcome row on a
rejection) and short-circuits the turn with a plain read-back confirmation —
Sally's "weld the claim to the artifact": the proof is the next render, never a
prose "✅ learned".

Placement: runs AFTER ``classify`` (so ``state.history`` carries the prior render)
and BEFORE ``execute``. On a handled reaction it stamps ``feedback_handled=True``
and the confirmation onto ``state.responses``; ``execute`` then short-circuits the
tool loop and ``deliver`` enforces the freshly-written style on the confirmation
itself ("the reply below already follows it").

Determinism + reuse (code-simplifier): the artifact→field mapping reuses the
EXACT LS2 transforms as detectors (``_strip_emphasis`` / ``tables_to_plain_list``
/ ``_title_bare_links``) so detection can never drift from enforcement. The write
path mirrors ``set_output_preference`` (read-merge-write the JSON record), scoped
to the per-(identity, channel) owner key the delivery seam reads. The negative
outcome row is recorded with ``success=False`` + a ``failure_class`` so the
positive-only learning corpus structurally excludes it (it is a correction /
telemetry record, NOT a negative lesson).

Fail-open (B5): NO classifier, NO history, an error, or a verdict that is not a
confident FORMAT reaction about the last render leaves the turn byte-identical —
a capture fault must never crash or alter a normal turn.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from stackowl.channels._format import (
    OUTPUT_STYLE_FIELDS,
    OUTPUT_STYLE_KEY,
    OutputStyle,
    _apply_outside_code,
    _strip_emphasis,
    _title_bare_links,
    load_output_style,
    tables_to_plain_list,
)
from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.interaction.feedback_classifier import FeedbackSignal
    from stackowl.memory.preferences import PreferenceStore

# A clarifying question is the ONE thing we surface when the classifier is sure it
# is format feedback but unsure of the polarity (never guess a wrong-polarity
# write). UI copy, not classification — same status as the existing clarify/floor
# strings; the LLM does the (multilingual) classification, we never word-match.
_CLARIFY_QUESTION = (
    "Do you want me to change how the last reply was formatted "
    "(for example: drop the asterisks, or show links as titles), "
    "or was that about something else?"
)

# FR-2 (de-complication PRD) — aspects captured as durable preference NOTES
# (verbatim user text), independent of the FORMAT/output_style path above.
# "overall" stays excluded (too vague to be an enforceable preference).
_NOTE_ASPECTS = frozenset({"content", "tone", "length"})

# FR-8 (de-complication PRD) — messages this long or longer skip the classifier
# LLM call entirely: reactions to a prior render are short; a message this long
# is a new task, and the classifier's own `referent != "last"` check would
# reject it anyway, but only after paying for the LLM call.
_PREFILTER_MAX_CHARS = 200


async def run(state: PipelineState) -> PipelineState:
    """Capture a confident FORMAT reaction to the last render into ``output_style``.

    Byte-identical no-op unless the turn is a confident, format-scoped reaction to
    the immediately-preceding agent message. Never raises (B5)."""
    log.gateway.debug(
        "[pipeline] feedback: entry",
        extra={"_fields": {"trace_id": state.trace_id, "session_id": state.session_id}},
    )
    services = get_services()
    classifier = services.feedback_classifier
    store = services.preference_store
    if classifier is None or store is None:
        log.gateway.debug(
            "[pipeline] feedback: classifier/store unwired — pass-through",
            extra={"_fields": {"has_classifier": classifier is not None,
                               "has_store": store is not None}},
        )
        return state

    render = _last_agent_render(state)
    if not render:
        return state  # nothing to react to → byte-identical

    if len(state.input_text) >= _PREFILTER_MAX_CHARS:
        log.gateway.debug(
            "[pipeline] feedback: message too long for a reaction — pass-through",
            extra={"_fields": {"trace_id": state.trace_id, "input_len": len(state.input_text)}},
        )
        return state  # a message this long is a new task, not a reaction (FR-8)

    try:
        result = await classifier.classify(
            user_message=state.input_text,
            last_agent_message=render,
        )
    except Exception as exc:  # B5 — a classifier fault must never crash the turn
        log.gateway.error(
            "[pipeline] feedback: classify raised — pass-through",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state

    # The reaction must point at the LAST render. ``referent != "last"`` covers the
    # fail-open abstain (referent "none") AND any non-adjacent reaction → no write,
    # byte-identical.
    if result.referent != "last":
        return state

    # Only the FORMAT aspect is mechanically enforceable (LS2). content / tone /
    # length are captured separately below as preference NOTES (FR-2); "overall"
    # stays a no-op write either way (too vague to be a specific preference).
    fmt = [s for s in result.signals
           if s.aspect == "format" and s.polarity in ("positive", "negative")]
    notes = [s for s in result.signals
             if s.aspect in _NOTE_ASPECTS and s.polarity in ("positive", "negative")]
    if not fmt and not notes:
        log.gateway.debug(
            "[pipeline] feedback: no enforceable signal — pass-through",
            extra={"_fields": {"trace_id": state.trace_id,
                               "signals": [(s.polarity, s.aspect) for s in result.signals]}},
        )
        return state

    owner_key = state.identity_key or state.session_id

    # FR-2 — confident content/tone/length reactions are captured as a durable
    # preference NOTE (verbatim state.input_text, aspect-keyed), independent of
    # the FORMAT path below: "good content but lose the asterisks" writes BOTH.
    # This never short-circuits the turn (no confirmation message) and never
    # writes on a low-confidence/abstain verdict (mirrors the format guard) —
    # the decision to write is 100% classifier-verdict-driven.
    if notes and not result.abstain:
        try:
            await _write_preference_notes(store, owner_key, notes, state.input_text)
            log.gateway.info(
                "[pipeline] feedback: preference note(s) captured",
                extra={"_fields": {"trace_id": state.trace_id, "owner_key": owner_key,
                                   "aspects": [s.aspect for s in notes]}},
            )
        except Exception as exc:  # B5 — a note-write fault must never crash the turn
            log.gateway.error(
                "[pipeline] feedback: preference note write failed — pass-through",
                exc_info=exc,
                extra={"_fields": {"trace_id": state.trace_id, "owner_key": owner_key}},
            )

    if not fmt:
        return state  # only the note path applied — the turn proceeds normally

    if result.abstain:
        # Confident it is FORMAT feedback but not of the polarity → ask one
        # question rather than guess a wrong-polarity write (the "you lost it"
        # regression is worse than a question).
        log.gateway.info(
            "[pipeline] feedback: abstain on format polarity — asking one question",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return _short_circuit(state, _CLARIFY_QUESTION)

    try:
        negative = any(s.polarity == "negative" for s in fmt)
        if negative:
            return await _handle_negative(state, services, store, owner_key, render)
        return await _handle_positive(state, store, owner_key, render)
    except Exception as exc:  # B5 — a write fault must never crash the turn
        log.gateway.error(
            "[pipeline] feedback: capture failed — pass-through",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id, "owner_key": owner_key}},
        )
        return state


# --------------------------------------------------------------------------- #
# Polarity handlers                                                           #
# --------------------------------------------------------------------------- #


async def _handle_negative(
    state: PipelineState, services: object, store: PreferenceStore,
    owner_key: str, render: str,
) -> PipelineState:
    """NEGATIVE/format — detect the offending artifact, set the suppressing field,
    record a (non-lesson) rejection outcome row, confirm by NAMING the defect."""
    changes, defects = _detect_defects(render)
    if not changes:
        # The user says it broke but no known artifact is present. Re-assert the
        # existing explicit style (durability) if any; otherwise we genuinely do not
        # know what broke → ask rather than guess.
        current = await load_output_style(store, owner_key)
        changes = _explicit_fields(current)
        if not changes:
            return _short_circuit(state, _CLARIFY_QUESTION)

    style = await _merge_write_style(store, owner_key, changes)
    await _record_rejection(services, state, render)
    log.gateway.info(
        "[pipeline] feedback: negative/format captured",
        extra={"_fields": {"trace_id": state.trace_id, "owner_key": owner_key,
                           "changes": changes, "defects": defects}},
    )
    return _short_circuit(state, _negative_confirmation(defects, style))


async def _handle_positive(
    state: PipelineState, store: PreferenceStore, owner_key: str, render: str,
) -> PipelineState:
    """POSITIVE/format — PIN the current effective style (or, if none is set, the
    clean attributes of the last render) so "keep doing this" becomes durable. No
    outcome row: a pin is not a rejection (positive-only corpus untouched)."""
    current = await load_output_style(store, owner_key)
    changes = _explicit_fields(current) or _infer_clean_style(render)
    style = await _merge_write_style(store, owner_key, changes)
    log.gateway.info(
        "[pipeline] feedback: positive/format pinned",
        extra={"_fields": {"trace_id": state.trace_id, "owner_key": owner_key,
                           "changes": changes}},
    )
    return _short_circuit(state, _positive_confirmation(style))


# --------------------------------------------------------------------------- #
# Deterministic artifact → field detection (reuses the LS2 transforms)        #
# --------------------------------------------------------------------------- #


def _has_emphasis(text: str) -> bool:
    """True iff the LS2 emphasis-stripper would change ``text`` (raw ``*``/``**``/
    ``_``/``~~`` outside code) — detection welded to the enforcement transform."""
    return _apply_outside_code(text, _strip_emphasis) != text


def _has_table(text: str) -> bool:
    """True iff a GFM pipe-table is present (the LS2 flatten would change it)."""
    return tables_to_plain_list(text) != text


def _has_bare_link(text: str) -> bool:
    """True iff a bare/untitled URL is present (the LS2 link-titler would wrap it)."""
    return _apply_outside_code(text, _title_bare_links) != text


def _detect_defects(render: str) -> tuple[dict[str, str], list[str]]:
    """Map detected offending artifacts in ``render`` to suppressing style fields.

    Returns ``(changes, defects)`` where ``changes`` is the format-key patch and
    ``defects`` is the plain-language list of what was found (for the confirmation).
    """
    changes: dict[str, str] = {}
    defects: list[str] = []
    if _has_emphasis(render):
        changes["markdown"] = "minimal"
        defects.append("asterisks")
    if _has_table(render):
        changes["tables"] = "off"
        defects.append("a raw table")
    if _has_bare_link(render):
        changes["links"] = "titles"
        defects.append("an untitled link")
    return changes, defects


def _infer_clean_style(render: str) -> dict[str, str]:
    """Pin the clean shape of a liked render with no prior style set.

    "no asterisks → keep them out (markdown minimal); no raw table → keep tables
    off; links already titled → keep titling." If the render legitimately used an
    artifact the user liked, the matching field stays at its keep-it default
    (markdown full / tables on / links inline)."""
    return {
        "markdown": "full" if _has_emphasis(render) else "minimal",
        "tables": "on" if _has_table(render) else "off",
        "links": "inline" if _has_bare_link(render) else "titles",
    }


def _explicit_fields(style: OutputStyle) -> dict[str, str]:
    """The non-default fields of ``style`` (what is actually being enforced)."""
    default = OutputStyle()
    return {f: getattr(style, f) for f in OUTPUT_STYLE_FIELDS
            if getattr(style, f) != getattr(default, f)}


async def _write_preference_notes(
    store: PreferenceStore, owner_key: str, signals: list[FeedbackSignal], text: str,
) -> None:
    """Persist each non-format signal as a durable preference NOTE (FR-2).

    ``text`` is the user's own reaction message verbatim — never a
    synthesized/templated phrase, so capture stays multilingual and adds no
    summarization step. One write per signal so "too long AND too formal"
    (two note-aspects in one message) persists both independently."""
    from stackowl.memory.preferences import write_preference_note

    for signal in signals:
        await write_preference_note(
            store, owner_key, aspect=signal.aspect, polarity=signal.polarity, text=text,
        )


# --------------------------------------------------------------------------- #
# Persistence (mirrors set_output_preference's read-merge-write)              #
# --------------------------------------------------------------------------- #


async def _merge_write_style(
    store: PreferenceStore, owner_key: str, changes: dict[str, str],
) -> OutputStyle:
    """Read-merge-write the ``output_style`` JSON for ``owner_key`` (format keys
    only). Mirrors ``set_output_preference._set_style`` but scoped to the
    per-(identity, channel) owner key the delivery seam reads."""
    existing_raw = await store.get(owner_key, OUTPUT_STYLE_KEY)
    existing: dict[str, object] = {}
    if existing_raw:
        try:
            loaded = json.loads(existing_raw)
            if isinstance(loaded, dict):
                existing = {k: v for k, v in loaded.items() if k in OUTPUT_STYLE_FIELDS}
        except (ValueError, TypeError):
            existing = {}  # corrupt prior record — overwrite cleanly
    merged = {**existing, **changes}
    style = OutputStyle.model_validate(merged)  # controlled tokens — validates
    await store.set(owner_key, OUTPUT_STYLE_KEY, json.dumps(merged))
    return style


async def _record_rejection(services: object, state: PipelineState, render: str) -> None:
    """Record a rejection outcome row (``success=False`` + ``failure_class``).

    The positive-only learners key on ``success=1 AND failure_class IS NULL``, so a
    failure-classed row is structurally excluded from the lessons/reflection corpora
    — this is a correction / telemetry record, NOT a negative lesson. Best-effort
    (B5): a missing pool or a write error is logged, never raised. Idempotent on the
    turn's trace_id, so the backend's end-of-run row is a no-op (this turn's outcome
    IS the captured rejection)."""
    db = getattr(services, "db_pool", None)
    if db is None:
        return
    try:
        from stackowl.memory.outcome_store import TaskOutcomeStore

        await TaskOutcomeStore(db).record(
            trace_id=state.trace_id,
            session_id=state.session_id,
            owl_name=state.owl_name,
            channel=state.channel,
            success=False,
            latency_ms=0.0,
            tool_call_count=0,
            failure_class="feedback_rejected",
            step_durations={},
            input_text=state.input_text,
            response_text=render,
        )
    except Exception as exc:  # B5 — telemetry must never crash the turn
        log.gateway.error(
            "[pipeline] feedback: rejection outcome write failed",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )


# --------------------------------------------------------------------------- #
# Confirmation (plain observable rule read-back — never "✅ learned")          #
# --------------------------------------------------------------------------- #


def _positive_confirmation(style: OutputStyle) -> str:
    """Read the pinned rule back plainly; the next render is the receipt."""
    rules = style.describe_rules()
    if rules:
        return (f"Got it — from now on here: {_join(rules)}. "
                "The reply below already follows it.")
    return "Got it — I'll keep formatting replies this way from now on."


def _negative_confirmation(defects: list[str], style: OutputStyle) -> str:
    """Name the exact detected defect, no cheer; state the now-enforced rule."""
    rules = style.describe_rules()
    rule_clause = f" From now on here: {_join(rules)}." if rules else ""
    if defects:
        return f"Last message had {_join(defects)} — fixed.{rule_clause}"
    return f"Fixed.{rule_clause}"


def _join(items: list[str]) -> str:
    """Join short clauses for a one-line confirmation ("a and b", "a, b and c")."""
    if len(items) <= 1:
        return items[0] if items else ""
    return f"{', '.join(items[:-1])} and {items[-1]}"


# --------------------------------------------------------------------------- #
# State helpers                                                                #
# --------------------------------------------------------------------------- #


def _last_agent_render(state: PipelineState) -> str:
    """The immediately-preceding assistant message — the render being reacted to.

    Read from ``state.history`` (populated by classify from staged conversation
    turns, oldest-first); the last ``role=="assistant"`` message is the prior
    reply. Empty string when there is no prior render (first turn)."""
    for msg in reversed(state.history):
        if getattr(msg, "role", None) == "assistant":
            content = getattr(msg, "content", "")
            return content if isinstance(content, str) else ""
    return ""


def _short_circuit(state: PipelineState, text: str) -> PipelineState:
    """Stamp the confirmation as the turn's single response + ``feedback_handled``.

    ``execute`` reads ``feedback_handled`` and skips the tool loop; ``deliver`` then
    enforces the freshly-written style on THIS confirmation (so "the reply below
    already follows it" is literally true)."""
    chunk = ResponseChunk(
        content=text, is_final=False, chunk_index=0,
        trace_id=state.trace_id, owl_name=state.owl_name,
    )
    return state.evolve(responses=(chunk,), feedback_handled=True)

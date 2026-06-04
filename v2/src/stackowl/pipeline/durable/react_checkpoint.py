"""ReActCheckpoint — the durable working-set snapshot for one ReAct loop (S1).

Captures the minimal state needed to resume a ReAct loop mid-run after a crash:

* ``iteration`` — monotonic counter of completed LLM rounds (= ``current_step``
  on the :class:`~stackowl.pipeline.durable.task.DurableTask`).  Seeded back
  into ``DurableReActContext.current_iteration`` on resume so
  idempotency-key step_index values are stable across drives.
* ``messages`` — the running provider-agnostic transcript list (list of plain
  dicts with at least a ``"role"`` key and some ``"content"``).  Both Anthropic
  and OpenAI providers accumulate a ``messages: list[dict[str, Any]]`` variable
  inside ``complete_with_tools`` that grows one entry per LLM round and one per
  tool-result user turn (anthropic_provider.py:230,252; openai_provider.py:
  232,248).  Stored as plain JSON-serializable dicts — never coupled to an SDK
  type.

  S5/resume caveat: for OpenAI the system prompt is stored as messages[0]
  (role=system); a resumer must restore this ``messages`` list directly into
  the provider loop, NOT re-inject the system prompt, or it will be duplicated.

* ``tool_call_records`` — the ``all_calls`` accumulator the providers build
  (anthropic:247; openai:251): one record per dispatched tool call with ``id``,
  ``name``, ``args``, ``result``, and ``failed`` keys.  Used for telemetry and
  for ``summarize_tool_outcomes`` on resume.

All three fields are JSON-serializable by design — no datetimes, no SDK objects,
no opaque bytes. The model is ``frozen=True`` (immutable) so checkpoints are
value-typed, composable, and safe to pass around without defensive copies.

:func:`serialize` / :func:`deserialize` provide a deterministic JSON round-trip.
Malformed blobs raise :class:`ReActCheckpointDecodeError` (logged; never
silently swallowed).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from stackowl.exceptions import CheckpointSchemaError

log = logging.getLogger("stackowl.tasks")

#: Current on-disk schema version for serialized :class:`ReActCheckpoint` blobs.
#: Bump this whenever the persisted shape changes incompatibly; ``deserialize``
#: rejects any blob whose ``schema_version`` is greater than this value.
CHECKPOINT_SCHEMA_VERSION = 1


class ReActCheckpointDecodeError(ValueError):
    """Raised when :func:`deserialize` encounters a malformed checkpoint blob."""


class ReActCheckpoint(BaseModel, frozen=True):
    """Immutable snapshot of a ReAct loop's working set.

    Field semantics are grounded in the real provider loops:

    ``iteration``
        Number of *completed* LLM rounds.  Incremented by
        ``on_iteration_complete`` at the *bottom* of each ``for``-loop body
        (after the tool-result user turn is appended).  On resume this is
        seeded into ``ctx.current_iteration`` so the first new round uses the
        correct step_index for ledger-key stability (design §2.4).

    ``messages``
        The ``messages`` list that both providers pass to the API.  Each entry
        is a plain dict with at minimum a ``"role"`` key (``"user"``,
        ``"assistant"``, ``"system"``).  Content may be a string or a list of
        typed content-blocks (Anthropic).  Everything is JSON-serializable.
        Only the budget-trimmed snapshot is stored (R4 mitigation: the provider
        already trims before each call via ``trim_messages_to_budget``).

    ``tool_call_records``
        The ``all_calls`` accumulator: one dict per dispatched tool call,
        carrying ``id``, ``name``, ``args``, ``result``, and ``failed``.
        Provider-agnostic (OpenAI uses ``id=None``; Anthropic uses a string id).
    """

    schema_version: int = Field(
        default=CHECKPOINT_SCHEMA_VERSION,
        ge=1,
        description=(
            "Durable-contract version of this serialized shape (Winston). "
            "Absent in legacy blobs => treated as version 1 (back-compat)."
        ),
    )
    iteration: int = Field(..., ge=0, description="Completed LLM round count (monotonic).")
    messages: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Provider-agnostic running transcript (plain JSON dicts).",
    )
    tool_call_records: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Accumulated tool-dispatch records (all_calls snapshot).",
    )


def serialize(cp: ReActCheckpoint) -> str:
    """Serialise a :class:`ReActCheckpoint` to a compact JSON string.

    The output is deterministic: keys are sorted so identical checkpoints
    always produce identical blobs (useful for content-based deduplication and
    testing).  The result is round-trip–safe through :func:`deserialize`.
    """
    # 1. ENTRY
    log.debug(
        "[tasks] react_checkpoint.serialize: entry",
        extra={"_fields": {"iteration": cp.iteration, "msg_count": len(cp.messages)}},
    )
    # 2. DECISION — use model_dump for Pydantic-validated dict, then sort keys
    payload = cp.model_dump()
    # 3. STEP — strict serialisation: no default=str coercion (lossy, non-round-trip-safe)
    try:
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    except TypeError as exc:
        log.error(
            "[tasks] react_checkpoint.serialize: payload is not JSON-serializable",
            exc_info=exc,
            extra={"_fields": {"iteration": cp.iteration, "msg_count": len(cp.messages)}},
        )
        raise ReActCheckpointDecodeError(
            f"checkpoint payload is not JSON-serializable: {exc}"
        ) from exc
    # 4. EXIT
    log.debug(
        "[tasks] react_checkpoint.serialize: exit",
        extra={"_fields": {"blob_len": len(blob)}},
    )
    return blob


def deserialize(blob: str) -> ReActCheckpoint:
    """Deserialise a JSON blob produced by :func:`serialize` back to a
    :class:`ReActCheckpoint`.

    Raises :class:`ReActCheckpointDecodeError` (and logs at ERROR) on any
    malformed input — no silent failure, no partial/degraded result.
    Validation is performed by Pydantic on construction so unexpected field
    types are caught early.
    """
    # 1. ENTRY
    log.debug(
        "[tasks] react_checkpoint.deserialize: entry",
        extra={"_fields": {"blob_len": len(blob) if isinstance(blob, str) else -1}},
    )
    # 2. DECISION — parse JSON first, then validate via Pydantic
    try:
        raw = json.loads(blob)
    except json.JSONDecodeError as exc:
        log.error(
            "[tasks] react_checkpoint.deserialize: invalid JSON — cannot restore checkpoint",
            exc_info=exc,
            extra={"_fields": {"blob_prefix": blob[:80] if isinstance(blob, str) else repr(blob)}},
        )
        raise ReActCheckpointDecodeError(
            f"checkpoint blob is not valid JSON: {exc}"
        ) from exc
    # 2b. VERSION GATE (Winston) — a versionless durable contract is unsafe.
    # Absent field => legacy blob => treat as version 1 (back-compat, no raise).
    # A version GREATER than this build's known version is forward-incompatible
    # and must fail loud rather than silently drop fields.
    if isinstance(raw, dict):
        found_version = raw.get("schema_version", CHECKPOINT_SCHEMA_VERSION)
        if not isinstance(found_version, int) or found_version > CHECKPOINT_SCHEMA_VERSION:
            log.error(
                "[tasks] react_checkpoint.deserialize: unknown schema_version — refusing restore",
                extra={"_fields": {
                    "found_version": found_version,
                    "current_version": CHECKPOINT_SCHEMA_VERSION,
                }},
            )
            raise CheckpointSchemaError(found_version, CHECKPOINT_SCHEMA_VERSION)
    # 3. STEP — Pydantic model validation (catches missing/wrong-typed fields)
    try:
        cp = ReActCheckpoint(**raw)
    except Exception as exc:
        log.error(
            "[tasks] react_checkpoint.deserialize: schema validation failed",
            exc_info=exc,
            extra={"_fields": {"raw_keys": list(raw.keys()) if isinstance(raw, dict) else "non-dict"}},
        )
        raise ReActCheckpointDecodeError(
            f"checkpoint blob failed schema validation: {exc}"
        ) from exc
    # 4. EXIT
    log.debug(
        "[tasks] react_checkpoint.deserialize: exit",
        extra={"_fields": {"iteration": cp.iteration, "msg_count": len(cp.messages)}},
    )
    return cp

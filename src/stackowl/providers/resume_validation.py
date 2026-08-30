"""Resume-transcript validation — pure, provider-neutral guard (B1 hardening).

Lives in ``providers/`` so both provider loops can import it WITHOUT any upward
dependency on ``pipeline.durable`` (B-boundary rule: providers must NOT import
pipeline.durable).  It only knows about plain transcript dicts and the two
provider wire-shapes — never a :class:`ReActCheckpoint` type.

Why this exists (defense-in-depth):
    The current ``checkpoint_callback`` writes the checkpoint AFTER tool results
    are appended to the running ``messages`` list, so a well-formed checkpoint
    NEVER dangles a half-dispatched tool call.  This validator therefore does
    not guard a known-broken path today — it is a fail-loud safety net for:
      * a future checkpoint-write site that snapshots mid-dispatch,
      * cross-provider resume (an Anthropic transcript resumed on OpenAI, etc.),
      * a hand-crafted / corrupted blob.
    Without it a malformed transcript reaches the provider API and surfaces as
    an opaque ``ProviderError`` 400; with it we fail with a typed
    :class:`ResumeTranscriptError` naming the exact defect.

Wire-shape reference:
    * OpenAI assistant tool call: ``{"role": "assistant", "tool_calls": [{"id": ...}]}``
      answered by ``{"role": "tool", "tool_call_id": <id>, ...}``.
    * Anthropic assistant tool call: ``{"role": "assistant", "content": [
      {"type": "tool_use", "id": ...}]}`` answered by a following user turn whose
      content contains ``{"type": "tool_result", "tool_use_id": <id>}``.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from stackowl.exceptions import ResumeTranscriptError

log = logging.getLogger("stackowl.engine")

ProviderKind = Literal["anthropic", "openai"]

#: Content of the synthetic assistant turn inserted by
#: :func:`close_interrupted_tool_sequence`. Deliberately plain text: it is
#: read by the model as the turn it never got to finish.
CLOSING_TURN_TEXT = "Operation interrupted."


def _is_tool_result_turn(message: dict[str, Any], provider_kind: str) -> bool:
    """Whether this turn IS a tool result (D01.5, Rule 5).

    The two wire shapes differ enough to be worth naming rather than inlining:

    * openai — a dedicated ``role: "tool"`` message.
    * anthropic — a ``user`` turn whose content blocks are ``tool_result``. The
      role is genuinely ``user`` there, which is exactly why this is easy to miss:
      the turn looks like an ordinary user message to anything checking roles
      alone, and only the block type distinguishes it.

    NOT expressible as ``bool(_anthropic_result_ids(message))``, though it looks
    like it should be — a future reader will see the near-duplicate block scan and
    want to merge them. That helper additionally requires ``tool_use_id`` to be
    present, because its job is to COLLECT ids for the matched-pairs check. A
    tool_result block missing its id is still SHAPED like a tool result, and Rule
    5 must reject a user turn following it; folding the two would silently stop
    Rule 5 firing on malformed blocks. Rule 4 catches the missing id separately.
    """
    if provider_kind == "openai":
        return message.get("role") == "tool"
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    )


def _openai_tool_call_ids(message: dict[str, Any]) -> list[str]:
    """Return the tool_call ids declared in an OpenAI assistant turn (or [])."""
    if message.get("role") != "assistant":
        return []
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return []
    ids: list[str] = []
    for tc in tool_calls:
        if isinstance(tc, dict) and "id" in tc and tc["id"] is not None:
            ids.append(str(tc["id"]))
    return ids


def _openai_result_id(message: dict[str, Any]) -> str | None:
    """Return the tool_call_id this OpenAI tool turn answers (or None)."""
    if message.get("role") != "tool":
        return None
    tcid = message.get("tool_call_id")
    return str(tcid) if tcid is not None else None


def _anthropic_tool_use_ids(message: dict[str, Any]) -> list[str]:
    """Return the tool_use ids declared in an Anthropic assistant turn (or [])."""
    if message.get("role") != "assistant":
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    ids: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id") is not None:
            ids.append(str(block["id"]))
    return ids


def _anthropic_result_ids(message: dict[str, Any]) -> list[str]:
    """Return the tool_use ids this Anthropic user turn answers (or [])."""
    if message.get("role") != "user":
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    ids: list[str] = []
    for block in content:
        if (
            isinstance(block, dict)
            and block.get("type") == "tool_result"
            and block.get("tool_use_id") is not None
        ):
            ids.append(str(block["tool_use_id"]))
    return ids


def validate_resume_transcript(
    messages: list[dict[str, Any]],
    *,
    provider_kind: ProviderKind,
) -> None:
    """Validate a resume transcript, raising :class:`ResumeTranscriptError` on defects.

    Rules enforced:
      1. The transcript is non-empty.
      2. (anthropic only) No message has ``role == "system"`` — Anthropic keeps
         the system prompt separate; a system turn in ``messages`` would 400.
      3. The LAST message is not a bare assistant turn with an UNANSWERED tool
         call (a dangling ``tool_use`` / ``tool_calls`` with no following result).
         Resuming mid-dispatch would 400; the dangling id(s) are reported.
      4. Every declared ``tool_use`` / ``tool_calls`` id has a matching result
         message somewhere later in the transcript (matched-pairs invariant).
      5. No ``user`` turn directly FOLLOWS a tool result (D01.5). An interrupted
         turn whose next user message lands on an unclosed tool sequence produces
         that pairing, and strict providers do not merely reject it — they
         continue the user's message instead of answering it.
         NOTE the asymmetry with rule 3: a transcript ENDING on a tool result is
         perfectly valid (the model is called next and answers it). Only the
         ``tool result -> user`` adjacency is a defect.

    Pure and side-effect-free apart from a debug entry/exit log.  Safe to call
    at the top of a provider's resume branch before the first API call.
    """
    # 1. ENTRY
    log.debug(
        "[providers] validate_resume_transcript: entry",
        extra={"_fields": {"provider_kind": provider_kind, "msg_count": len(messages)}},
    )

    # Rule 1 — empty transcript
    if not messages:
        raise ResumeTranscriptError("transcript is empty")

    if provider_kind == "anthropic":
        call_ids = _anthropic_tool_use_ids
        # For matched-pairs we collect result ids per message (each user turn may
        # answer several tool_use blocks at once).
        def result_ids(m: dict[str, Any]) -> list[str]:
            return _anthropic_result_ids(m)

        # Rule 2 — no system role in an Anthropic transcript
        for m in messages:
            if m.get("role") == "system":
                raise ResumeTranscriptError(
                    "anthropic transcript contains a system-role message "
                    "(system must be passed separately, not in messages)"
                )
    else:  # openai
        call_ids = _openai_tool_call_ids

        def result_ids(m: dict[str, Any]) -> list[str]:
            rid = _openai_result_id(m)
            return [rid] if rid is not None else []

    # Collect every answered id across the WHOLE transcript (results may appear
    # in any message that follows the call).
    answered: set[str] = set()
    for m in messages:
        answered.update(result_ids(m))

    # Rule 4 — matched-pairs invariant: every declared call id is answered.
    declared: list[str] = []
    for m in messages:
        declared.extend(call_ids(m))
    unmatched = [cid for cid in declared if cid not in answered]
    if unmatched:
        raise ResumeTranscriptError(
            "transcript has tool call(s) with no matching result message",
            dangling_ids=unmatched,
        )

    # Rule 3 — the LAST turn must not be a bare assistant turn that dangles a
    # tool call.  (Subsumed by rule 4 when truly dangling, but reported with a
    # last-turn-specific message because resuming mid-dispatch is the canonical
    # failure mode and deserves the clearest diagnostic.)
    last = messages[-1]
    last_call_ids = call_ids(last)
    if last_call_ids:
        # Anything declared on the final turn cannot have a later result.
        raise ResumeTranscriptError(
            "transcript ends on an assistant turn with an unanswered tool call "
            "(resuming mid-dispatch would 400)",
            dangling_ids=last_call_ids,
        )

    # Rule 5 (D01.5, NARROWED 2026-08-30) — ANTHROPIC ONLY.
    #
    # The original rule rejected a tool result followed by a user turn on BOTH
    # provider kinds. That was too wide, and it cost a live task: on 2026-08-30
    # at 03:20:22 trace `recover-4e6044f0cde9` was refused here, surfaced as
    # "critical step failed with no response", and died having delivered nothing.
    # Re-running this validator over every checkpoint in the live tasks table
    # found 12 more in exactly the same state — all of them `status='failed'`.
    #
    # WHY THE WIDE RULE WAS WRONG. On the OpenAI wire a tool result is its own
    # `{"role": "tool"}` message, and `tool -> user` is a legal, ordinary
    # sequence — it is what THIS PLATFORM SENDS on every round that folds a
    # directive in (steering, budget-converge, progress-nudge, and the native
    # per-iteration directive all splice `{"role": "user", ...}` immediately
    # after the tool results). The refused checkpoints show the model answering
    # that directive in the very next turn. The wide rule was therefore
    # rejecting the loop's own working transcript on resume — a guard failing
    # the shape it was built to protect.
    #
    # WHY IT IS STILL RIGHT FOR ANTHROPIC. There a tool_result rides INSIDE a
    # user turn, so the same adjacency is two CONSECUTIVE USER TURNS, which
    # breaks the strict alternation Anthropic requires. That is the real
    # incident the reference platform recorded (its agent/turn_finalizer.py:278,
    # incident #48879): the model continues the user's message instead of
    # answering it. `merge_consecutive_roles` exists for this but is NOT applied
    # in the provider tool loop (measured 2026-08-30: its only callers are
    # pipeline/steps/execute.py and classify.py), so the checkpoint really can
    # carry the break.
    #
    # And it is now a REPAIRABLE defect rather than a fatal one — see
    # close_interrupted_tool_sequence below, which the resume load point applies
    # before this validator ever runs.
    if provider_kind == "anthropic":
        for i in range(len(messages) - 1):
            if not _is_tool_result_turn(messages[i], provider_kind):
                continue
            nxt = messages[i + 1]
            if nxt.get("role") == "user" and not _is_tool_result_turn(nxt, provider_kind):
                raise ResumeTranscriptError(
                    "transcript puts a user message directly after a tool result "
                    "(two consecutive user turns on the Anthropic wire; the model "
                    "continues the user's message instead of answering it) — the "
                    "tool sequence must be closed by an assistant turn first",
                )

    # 4. EXIT
    log.debug(
        "[providers] validate_resume_transcript: exit — ok",
        extra={"_fields": {"provider_kind": provider_kind, "declared_calls": len(declared)}},
    )


def infer_provider_kind(messages: list[dict[str, Any]]) -> ProviderKind:
    """Infer which wire shape a resume transcript is written in.

    The resume LOAD point (``pipeline.durable.recovery``) reconstructs a
    pipeline state long before a provider is resolved, so it cannot be told the
    kind — but the transcript already says which it is, and that is the same
    information. Kept HERE, beside the shape helpers, so the wire-shape
    knowledge has exactly one home.

    An OpenAI tool result is its own ``{"role": "tool"}`` message; an Anthropic
    one rides inside a user turn as a ``tool_result`` content block. Defaults to
    ``"openai"`` when the transcript contains no tool results at all — the
    conservative choice, since the Anthropic-only repair is then a no-op on a
    transcript that has nothing to repair.
    """
    for m in messages:
        if m.get("role") == "tool":
            return "openai"
        if _is_tool_result_turn(m, "anthropic"):
            return "anthropic"
    return "openai"


def close_interrupted_tool_sequence(
    messages: list[dict[str, Any]],
    *,
    provider_kind: ProviderKind,
) -> int:
    """Repair a transcript that lands a user turn on an unclosed tool sequence.

    This is the half of D01.5 that was scoped and never built. Its brainstorm
    recorded the goal as "add the missing rule (a Rule 5) PLUS A CLOSER, so an
    interrupted checkpoint is RESUMABLE rather than rejected" — only the rule
    shipped, so the first live firing killed its task instead of healing it.

    Ported from the reference platform's ``close_interrupted_tool_sequence``,
    which closes the sequence when PERSISTING an interrupted turn. We cannot
    close at persist time: the checkpoint is written by the FIRST callback in
    the compose chain, before the directive-folding callbacks return the very
    user turn that creates the adjacency, so the poison always arrives one
    checkpoint later. We therefore close at the single resume LOAD point
    instead, which is one place rather than four splice sites.

    Only ``anthropic`` transcripts can carry the defect (see Rule 5 above); an
    OpenAI ``tool -> user`` sequence is legal and is left untouched.

    Args:
        messages: the resume transcript. MUTATED IN PLACE.
        provider_kind: the wire shape to interpret ``messages`` as.

    Returns:
        The number of synthetic assistant turns inserted (0 when already sound).
    """
    # 1. ENTRY
    log.debug(
        "[providers] close_interrupted_tool_sequence: entry",
        extra={"_fields": {"provider_kind": provider_kind, "messages": len(messages)}},
    )
    # 2. DECISION — nothing to repair off the Anthropic wire.
    if provider_kind != "anthropic":
        return 0
    # 3. STEP — walk backwards so each insertion cannot shift an index we have
    #    yet to examine.
    inserted = 0
    for i in range(len(messages) - 2, -1, -1):
        if not _is_tool_result_turn(messages[i], provider_kind):
            continue
        nxt = messages[i + 1]
        if nxt.get("role") == "user" and not _is_tool_result_turn(nxt, provider_kind):
            messages.insert(i + 1, {"role": "assistant", "content": CLOSING_TURN_TEXT})
            inserted += 1
    # 4. EXIT — INFO, not debug: this line is the evidence that the repair ran,
    #    and production runs at INFO. A repair nobody can see did not happen.
    if inserted:
        log.info(
            "[providers] close_interrupted_tool_sequence: closed an interrupted "
            "tool sequence — transcript is resumable",
            extra={"_fields": {"provider_kind": provider_kind, "inserted": inserted,
                               "messages": len(messages)}},
        )
    return inserted

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

    # 4. EXIT
    log.debug(
        "[providers] validate_resume_transcript: exit — ok",
        extra={"_fields": {"provider_kind": provider_kind, "declared_calls": len(declared)}},
    )

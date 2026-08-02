"""D01.2 — name the CAUSE of a cache invalidation, not just the symptom.

``prompt_metrics`` (D01.6) reports that the prompt CHANGED. It cannot report
WHICH part changed, and it says nothing about the tools array at all — which
matters because Anthropic renders ``tools`` at position 0, before ``system``, so
a tools array that varies per turn invalidates every downstream breakpoint on
every turn. The symptom was already visible; the cause was not.

**Why the tools audit lives on the shared pipeline path, not in the provider.**
The design put this in the Anthropic provider's observability table. It is here
instead, and deliberately: ``execute.py`` builds the schemas the same way for
EVERY protocol, and this deployment runs an OpenAI-protocol gateway. An audit on
the Anthropic path would have measured exactly nothing on the one box where the
question is being asked. Its whole purpose is to turn D01.3's premise — "the
per-turn context budget varies the tools array" — from an assertion into a
measurement, and an assertion that cannot fire is the trap D01.1 already taught
us about.

D05.2 has since removed both causes of that variance: the ordering came from the
turn's ``request_text``, and the budget shrank as history grew. This audit is
therefore now the ACCEPTANCE TEST for that item rather than its diagnosis — it
should stay silent for any ``(lane, owl)`` pair with two or more turns. Read the
denominator before reading the result: silence with no repeat turns is no
opportunity, not success.

Reports only. Nothing here changes a request; a change is logged and the turn
proceeds. Measurement must never become an outage.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any

from stackowl.infra.observability import log
from stackowl.infra.prompt_invalidation import take_expected_change
from stackowl.infra.prompt_metrics import digest

# Bound on the per-lane maps. A long-lived server sees unbounded lanes, so the
# oldest entry is FIFO-evicted past this many. A lane that old has finished; the
# cost of forgetting it is one un-reported change on its next turn, which is
# strictly better than growing forever.
_LANE_CACHE_MAX = 512

# Keyed (session_key, owl_name), NOT session_key alone. Found in the validate
# stage on real traffic: an incident lane runs the staged RCA's three owls
# against ONE session_key, and their tool sets and personas differ BY DESIGN
# (invariant I6 — the same reason D01.1's prompt cache carries owl_name in its
# key). Lane-keyed, this audit reported three correct prompts as three
# violations, which is DEBT-21's mistake exactly: "grouped by session_id alone,
# three correct prompts read as three violations". An audit that cries wolf on
# every multi-owl lane trains its reader to ignore it.
_tools_hashes: OrderedDict[tuple[str, str], str] = OrderedDict()
_part_hashes: OrderedDict[tuple[str, str], dict[str, str]] = OrderedDict()


def reset_audit_state() -> None:
    """Forget every tracked lane. For tests — never called in production."""
    _tools_hashes.clear()
    _part_hashes.clear()


def _remember(
    store: OrderedDict[tuple[str, str], Any], key: tuple[str, str], value: Any
) -> None:
    store[key] = value
    store.move_to_end(key)
    while len(store) > _LANE_CACHE_MAX:
        store.popitem(last=False)


def tools_digest(tool_schemas: list[dict[str, Any]]) -> str:
    """Stable digest of the tools array AS IT WILL BE SENT.

    Order is significant and is NOT normalised away: the cached bytes include the
    array's order, so a reordered array with identical members still invalidates
    position 0. Sorting keys WITHIN each schema is safe (dict order is not part of
    the JSON the SDK emits); sorting the array itself would hide a real
    invalidation.
    """
    try:
        return digest(json.dumps(tool_schemas, ensure_ascii=False, sort_keys=True))
    except (TypeError, ValueError) as exc:
        # An unserialisable schema is a measurement problem, never a turn-breaking
        # one — but it is logged, because a silently un-audited tools array would
        # look identical to a stable one.
        log.engine.error(
            "[cache] breakpoints: tools array not serialisable — stability unmeasured",
            exc_info=exc,
        )
        return ""


def audit_tools_stability(
    session_key: str, tool_schemas: list[dict[str, Any]], owl: str = ""
) -> None:
    """Report when a lane's tools array changes between turns.

    A change is a WARNING rather than an error: it is not a fault, it is a cost.
    The array is legitimately allowed to vary (a delegated child has spawn tools
    removed; ``restrict_to`` narrows it), and the point is that the price of that
    variation should be visible rather than silent.

    Never raises.
    """
    if not session_key:
        # Background and utility calls legitimately have no lane. Bucketing them
        # all under "" would report a change on nearly every one of them — noise
        # that would train a reader to ignore this line.
        return
    current = tools_digest(tool_schemas)
    if not current:
        return
    key = (session_key, owl)
    previous = _tools_hashes.get(key)
    _remember(_tools_hashes, key, current)
    if previous is None or previous == current:
        return
    log.engine.warning(
        "[cache] breakpoints: tools array CHANGED — position 0 invalidated this turn",
        extra={"_fields": {
            "session_key": session_key,
            "owl": owl,
            "prev_hash": previous,
            "hash": current,
            "tool_count": len(tool_schemas),
            "see": "D01.3 — per-turn tool-schema selection",
        }},
    )


def audit_prompt_parts(
    session_key: str, parts: dict[str, str], owl: str = ""
) -> None:
    """Report WHICH system-prompt part changed between two builds on one lane.

    ``prompt_hash`` says the prompt moved; this says it was the skills catalogue,
    or the persona, or the profile. That difference is the whole gap between
    knowing a cache was lost and knowing what to fix — D01.1 spent a slice
    discovering the culprit was per-turn memory recall, by hand.

    Never raises.
    """
    if not session_key:
        return
    try:
        current = {name: digest(text) for name, text in parts.items()}
    except Exception as exc:  # never let an audit cost a turn
        log.engine.error(
            "[cache] breakpoints: prompt part audit failed — parts unmeasured",
            exc_info=exc, extra={"_fields": {"session_key": session_key}},
        )
        return
    key = (session_key, owl)
    previous = _part_hashes.get(key)
    _remember(_part_hashes, key, current)
    if previous is None:
        return
    changed = sorted(
        name for name, value in current.items() if previous.get(name, value) != value
    )
    if not changed:
        return
    # D01.4 — a change the user ASKED for is not an invalidator. Without this,
    # every deliberate edit would warn about itself, and an audit that cries wolf
    # on the commonest cause of a rebuild is one people learn to ignore. The
    # explanation is CONSUMED, so a single edit cannot blind the audit for good.
    expected = take_expected_change(owl)
    if expected is not None:
        log.engine.info(
            "[cache] breakpoints: prompt part changed as requested",
            extra={"_fields": {
                "session_key": session_key, "owl": owl,
                "parts": changed, "cause": expected,
            }},
        )
        return
    log.engine.warning(
        "[cache] breakpoints: prompt part CHANGED — the cached prefix is lost from here",
        extra={"_fields": {
            "session_key": session_key,
            "owl": owl,
            "parts": changed,
            "part_count": len(current),
        }},
    )

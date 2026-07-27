"""Unit tests for the system prompt: charter + the two operational TIERS.

The system prompt is three pure functions:

  * ``behavioral_charter()`` — DURABLE, GLOBAL, HIGH-LEVEL behavioural principles.
    It is timeless: valid on any model, OS, or tool set. It must therefore
    contain NO tool names, NO date, and NO case-specific example domains.
  * ``stable_operational_context()`` — the mechanics that do not change between
    turns: the generic ReAct call protocol, whose format must match the Phase A1
    parser (``providers/_react.parse_react_action``), and the downloads
    convention. Frozen per session (D01.1).
  * ``volatile_turn_context(now)`` — what belongs to ONE turn: the wall-clock,
    and the "no capabilities this turn" prohibition. Delivered with the turn.

These tests previously targeted ``operational_adapter(now)``, which held all of
the above in one string and was removed in D01.1's cleanup once both callers had
moved. They were RETARGETED rather than deleted: what they assert — chiefly that
the taught ``ACTION:`` format stays in lock-step with the real parser — is a
property of the text, not of the function that used to hold it. Byte-exact
snapshots of both tiers live in ``test_prompt_tiers.py``.
"""

from __future__ import annotations

from datetime import datetime

from stackowl.owls.base_prompt import (
    behavioral_charter,
    stable_operational_context,
    volatile_turn_context,
)


def _fixed() -> datetime:
    return datetime(2026, 5, 31, 14, 30, 0)


# Tokens the charter must NEVER contain. Tool names actually used in StackOwl
# prompts + example-domain words. The charter is pure behaviour, so all of these
# are forbidden (case-insensitive).
_FORBIDDEN_TOKENS = [
    # tool names
    "web_search",
    "skill_manage",
    "browser",
    "shell",
    "reflect_now",
    "tool_build",
    "download",
    "action:",  # the ReAct call keyword belongs to the adapter, not the charter
    # example-domain / case-specific words
    "news",
    "instagram",
    "video",
    "iso",
]


def test_charter_is_global() -> None:
    """The charter teaches principles only — and contains zero tool names or
    case-specific domain words."""
    charter = behavioral_charter()
    lowered = charter.lower()

    # Principle phrases are present (in our own wording).
    assert "never" in lowered and (
        "excuse" in lowered or "limitation" in lowered or "cutoff" in lowered
    ), "charter must carry the no-excuses principle"
    assert "stale" in lowered or "ground" in lowered or "verif" in lowered, (
        "charter must carry the grounding/evidence principle"
    )
    assert "build" in lowered or "learn" in lowered, (
        "charter must carry the self-extension principle"
    )
    assert "ownership" in lowered or "deliver" in lowered, (
        "charter must carry the take-ownership / deliver principle"
    )

    # And it is pure behaviour: no tool names, no example domains.
    for token in _FORBIDDEN_TOKENS:
        assert token not in lowered, (
            f"charter must NOT contain case-specific token {token!r} — it must be "
            "global and tool-agnostic"
        )


def test_charter_carries_direct_means_and_deliver_result() -> None:
    """The charter must steer toward the most direct/programmatic means over
    operating an interface by hand, and toward delivering the finished result
    itself — never handing back a link or manual steps for the user to do.

    Asserted on robust substrings (essence), not brittle full sentences, so the
    wording can evolve. Must also introduce no forbidden tool/domain token.
    """
    charter = behavioral_charter()
    lowered = charter.lower()

    # (a) Prefer the most direct means over a hands-on interactive/visual UI.
    assert "direct" in lowered, "charter must prefer the most direct means"
    assert "running code or commands" in lowered, (
        "charter must name composing capabilities directly (running code or commands)"
    )
    assert "interactive interface" in lowered or "visual interface" in lowered, (
        "charter must contrast direct means against operating an interface by hand"
    )

    # (b) Deliver the finished result itself — never a link or manual procedure.
    assert "deliver the finished result" in lowered, (
        "charter must require delivering the finished result itself"
    )
    assert "link" in lowered and (
        "manual procedure" in lowered or "instructions" in lowered
    ), "charter must forbid handing back a link or manual steps for the user to do"

    # Essence still pure behaviour: no forbidden tool/domain tokens introduced.
    for token in _FORBIDDEN_TOKENS:
        assert token not in lowered, (
            f"direct-means principle must NOT introduce case-specific token "
            f"{token!r}"
        )


def test_charter_carries_persistent_memory_principle() -> None:
    """The charter must declare the assistant has PERSISTENT memory surviving
    across conversations/restarts, and frame recalling-before-answering and
    durably preserving on request as ACTIONS it takes.

    Asserted on robust substrings (essence), not brittle full sentences, so the
    wording can evolve. Must introduce no forbidden tool/domain token.
    """
    charter = behavioral_charter()
    lowered = charter.lower()

    assert "persistent" in lowered, (
        "charter must declare the assistant has persistent memory"
    )
    assert "memory" in lowered, "charter must name its memory"
    assert "across conversations" in lowered or "across sessions" in lowered, (
        "charter must say the memory survives across conversations/sessions"
    )
    assert "recall" in lowered, (
        "charter must instruct recalling what it knows before answering"
    )
    assert "remember" in lowered or "preserve" in lowered, (
        "charter must instruct durably preserving what it is asked to remember"
    )

    # Essence still pure behaviour: no forbidden tool/domain tokens introduced.
    for token in _FORBIDDEN_TOKENS:
        assert token not in lowered, (
            f"memory principle must NOT introduce case-specific token {token!r}"
        )


def test_charter_carries_act_first_ambiguity_principle() -> None:
    """F-53: the act-first / anti-over-clarify principle is UNCONDITIONAL — it
    lives in the durable charter (every owl gets it), not gated behind a high
    curiosity trait band in the DNA injector. It must reserve clarifying for
    irreversible/expensive ambiguity and otherwise act on the most likely intent
    and state the assumption — introducing no forbidden tool/domain token.
    """
    charter = behavioral_charter().lower()

    assert "most likely" in charter, (
        "charter must carry the act-on-most-likely-intent principle"
    )
    assert "irreversible" in charter, (
        "charter must reserve clarifying questions for irreversible actions"
    )
    assert "assumption" in charter, (
        "charter must instruct stating the assumption when acting on ambiguity"
    )

    for token in _FORBIDDEN_TOKENS:
        assert token not in charter, (
            f"act-first principle must NOT introduce case-specific token {token!r}"
        )


def test_date_is_human_readable_and_lives_in_the_volatile_tier() -> None:
    """The date renders human-readably (not raw isoformat), and it renders in the
    VOLATILE tier — the stable tier must carry no clock at all, which is the
    property that lets the prompt be frozen for a whole session."""
    volatile = volatile_turn_context(_fixed())

    # Human-readable date: month name + year present.
    assert "May" in volatile
    assert "2026" in volatile

    # NOT the raw isoformat form ("2026-05-31T14:30:00").
    assert _fixed().isoformat() not in volatile

    # And the frozen tier is clock-free.
    assert "May" not in stable_operational_context()
    assert "2026" not in stable_operational_context()


def test_stable_tier_teaches_the_protocol() -> None:
    """The ReAct call protocol is STABLE-tier: how to call a tool does not vary
    between turns, so it can live inside the cached prefix."""
    stable = stable_operational_context()

    assert "ACTION:" in stable
    assert "```json" in stable


def test_protocol_example_parses_with_real_parser() -> None:
    """The taught ReAct format must match the real A1 parser's grammar.

    The example deliberately uses placeholders (``<name>`` / ``<arg>``) so it is
    NOT tied to any real tool. ``<name>`` does not match the parser's
    ``[a-z0-9_]+`` tool-name grammar, so ``parse_react_action`` returns ``None``
    for the placeholder example. We therefore assert the example is
    STRUCTURALLY what the parser expects: an ``ACTION:`` line followed by a
    fenced ``json`` block — the exact two tokens the parser keys on.

    This is the lock-step guard between the text we teach and the parser that
    reads it back. It moved from ``operational_adapter`` to the stable tier
    unchanged, because the format did.
    """
    from stackowl.providers._react import parse_react_action

    stable = stable_operational_context()

    # The placeholder example is intentionally non-tool-specific.
    assert parse_react_action(stable) is None, (
        "placeholder <name> must NOT resolve to a real tool"
    )

    # Structural match: the format the parser keys on is present verbatim.
    assert "ACTION:" in stable
    assert "```json" in stable

    # Proof the SAME format with a concrete tool name does parse — i.e. the
    # taught grammar is the parser's grammar.
    concrete = stable.replace("<name>", "example_tool").replace(
        '{"<arg>": "<value>"}', '{"arg": "value"}'
    )
    parsed = parse_react_action(concrete)
    assert parsed is not None
    name, args = parsed
    assert name == "example_tool"
    assert args == {"arg": "value"}


def test_tool_free_turn_drops_protocol_and_states_the_prohibition() -> None:
    """A turn offering no capabilities must both DROP the ACTION: format and
    STATE the prohibition — and the two halves live in different tiers.

    Dropping the taught format stops a less-instruction-following model imitating
    it with nothing real to call (the live incident: a plain conversational reply
    flagged/floored as an unparsed tool-call). But omission alone is not enough —
    live incident 2026-07-16 round 2 showed a natively tool-trained model
    attempting its OWN inherent function-calling convention on a turn with zero
    tools offered. The explicit negative instruction overrides that, and it is
    VOLATILE ("this turn"), so it cannot sit in the frozen tier.
    """
    stable = stable_operational_context(describe_tool_protocol=False)
    volatile = volatile_turn_context(_fixed(), capabilities_offered=False)

    # Stable half: no taught format, but the durable downloads rule remains.
    assert "ACTION:" not in stable
    assert "```json" not in stable
    assert "downloads" in stable.lower()

    # Volatile half: the prohibition, plus the turn's clock.
    assert "no capabilities are available" in volatile.lower()
    assert "do not attempt to call" in volatile.lower()
    assert "May" in volatile and "2026" in volatile

    # The prohibition must NOT leak into the frozen tier.
    assert "no capabilities are available" not in stable.lower()


def test_stable_tier_carries_downloads_convention() -> None:
    """The stable tier steers downloads into the workspace downloads/ folder —
    generic file-hygiene mechanics, no tool/domain words. The charter must NOT
    carry this (it stays pure behaviour)."""
    stable = stable_operational_context().lower()
    charter = behavioral_charter().lower()

    # The operational tier names the workspace downloads convention.
    assert "downloads/" in stable
    assert "workspace" in stable

    # It is allowed to use the word "download" (operational layer); the charter
    # is not — the forbidden-token test already guards the charter. Re-assert the
    # charter does not pick up the convention.
    assert "downloads/" not in charter

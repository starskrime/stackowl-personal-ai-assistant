"""Unit tests for the system prompt split into charter + adapter (Phase A+).

The system prompt is now two pure functions:

  * ``behavioral_charter()`` — DURABLE, GLOBAL, HIGH-LEVEL behavioural principles.
    It is timeless: valid on any model, OS, or tool set. It must therefore
    contain NO tool names, NO date, and NO case-specific example domains.
  * ``operational_adapter(now)`` — the SWAPPABLE mechanics: today's date as a
    human-readable grounding fact, plus the generic ReAct call protocol whose
    format must match the Phase A1 parser (``providers/_react.parse_react_action``).

``build_base_prompt(now)`` composes the two (charter + adapter) and keeps its
name so ``pipeline/steps/assemble.py`` continues to work unchanged.
"""

from __future__ import annotations

from datetime import datetime

from stackowl.owls.base_prompt import (
    behavioral_charter,
    build_base_prompt,
    operational_adapter,
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


def test_adapter_has_date_and_protocol() -> None:
    """The adapter renders today's date human-readably (not raw isoformat) and
    teaches the ReAct call protocol."""
    adapter = operational_adapter(_fixed())

    # Human-readable date: month name + year present.
    assert "May" in adapter
    assert "2026" in adapter

    # NOT the raw isoformat form ("2026-05-31T14:30:00").
    assert _fixed().isoformat() not in adapter

    # ReAct protocol tokens.
    assert "ACTION:" in adapter
    assert "```json" in adapter


def test_adapter_example_parses_with_real_parser() -> None:
    """The taught ReAct format must match the real A1 parser's grammar.

    The example deliberately uses placeholders (``<name>`` / ``<arg>``) so it is
    NOT tied to any real tool. ``<name>`` does not match the parser's
    ``[a-z0-9_]+`` tool-name grammar, so ``parse_react_action`` returns ``None``
    for the placeholder example. We therefore assert the example is
    STRUCTURALLY what the parser expects: an ``ACTION:`` line followed by a
    fenced ``json`` block — the exact two tokens the parser keys on.
    """
    from stackowl.providers._react import parse_react_action

    adapter = operational_adapter(_fixed())

    # The placeholder example is intentionally non-tool-specific.
    assert parse_react_action(adapter) is None, (
        "placeholder <name> must NOT resolve to a real tool"
    )

    # Structural match: the format the parser keys on is present verbatim.
    assert "ACTION:" in adapter
    assert "```json" in adapter

    # Proof the SAME format with a concrete tool name does parse — i.e. the
    # taught grammar is the parser's grammar.
    concrete = adapter.replace("<name>", "example_tool").replace(
        '{"<arg>": "<value>"}', '{"arg": "value"}'
    )
    parsed = parse_react_action(concrete)
    assert parsed is not None
    name, args = parsed
    assert name == "example_tool"
    assert args == {"arg": "value"}


def test_build_base_prompt_composes() -> None:
    """build_base_prompt(dt) contains both charter and adapter content."""
    dt = _fixed()
    prompt = build_base_prompt(dt)

    charter = behavioral_charter()
    adapter = operational_adapter(dt)

    # A distinctive slice of each must appear in the composed prompt.
    assert charter[:40] in prompt
    assert adapter[:40] in prompt  # adapter content is composed in
    assert "ACTION:" in prompt  # from adapter
    assert "2026" in prompt  # from adapter
    # Charter leads the adapter (strongest, durable signal first).
    assert prompt.index(charter[:40]) < prompt.index("ACTION:")

"""GatewayScanner slash-command routing — covering leading whitespace + contract tests.

Commit 3 fix: _SLASH_CMD_RE changed from ``^/(\\w+)`` to ``^\\s*/(\\w+)`` so that
messages with leading spaces or tabs still route as commands rather than falling
through silently to the secretary LLM.

Battery:
  '/help'          → command (no whitespace, baseline)
  ' /help'         → command (one leading space — was broken before fix)
  '\t/help'        → command (leading tab — was broken before fix)
  '/help@Bot'      → command (bot-suffix on /command is stripped by word-char stop)
  '/provider list' → command (args after command name; target='provider')
  '/unknowncmd'    → command (unknown commands still route to dispatcher;
                     dispatcher returns "Unknown slash command", not LLM turn)
  'hello'          → owl/secretary (plain text, NOT a command)
  '@hoot hi'       → owl route (not a command)
"""

from __future__ import annotations

from stackowl.gateway.scanner import GatewayScanner, IngressMessage, RouteDecision


def _msg(text: str) -> IngressMessage:
    return IngressMessage(
        text=text,
        session_id="sess-test",
        channel="cli",
        trace_id="trace-test",
    )


def _scan(text: str) -> RouteDecision:
    return GatewayScanner().scan(_msg(text))


# ---------------------------------------------------------------------------
# Slash command routing
# ---------------------------------------------------------------------------


def test_slash_help_no_whitespace_routes_command() -> None:
    d = _scan("/help")
    assert d.route == "command"
    assert d.target == "help"


def test_slash_help_leading_space_routes_command() -> None:
    """Leading space must NOT fall through to LLM — core fix for Commit 3."""
    d = _scan(" /help")
    assert d.route == "command"
    assert d.target == "help"


def test_slash_help_leading_tab_routes_command() -> None:
    """Leading tab must NOT fall through to LLM."""
    d = _scan("\t/help")
    assert d.route == "command"
    assert d.target == "help"


def test_slash_help_multiple_leading_spaces_routes_command() -> None:
    d = _scan("   /help")
    assert d.route == "command"
    assert d.target == "help"


def test_slash_help_with_bot_suffix_routes_command() -> None:
    """/help@Bot-style messages (Telegram group commands) route correctly."""
    d = _scan("/help@MyBot")
    assert d.route == "command"
    assert d.target == "help"


def test_slash_provider_list_routes_command_with_correct_target() -> None:
    """Args after the command word don't affect routing; target is the first word."""
    d = _scan("/provider list")
    assert d.route == "command"
    assert d.target == "provider"


def test_slash_unknown_command_still_routes_command() -> None:
    """An unrecognised /word routes to 'command', not LLM.

    The dispatcher will return 'Unknown slash command' — this is the contract.
    If this were to route to the secretary instead, the user would get an LLM
    response for a typo'd slash command, which is wrong.
    """
    d = _scan("/unknowncmd")
    assert d.route == "command"
    assert d.target == "unknowncmd"


def test_slash_unknown_with_leading_space_routes_command() -> None:
    d = _scan(" /unknowncmd")
    assert d.route == "command"
    assert d.target == "unknowncmd"


# ---------------------------------------------------------------------------
# Non-command routing (regression guard — these must NOT route to command)
# ---------------------------------------------------------------------------


def test_plain_text_routes_to_secretary() -> None:
    d = _scan("hello")
    assert d.route == "owl"
    assert d.target == "secretary"


def test_at_owl_routes_to_owl() -> None:
    d = _scan("@hoot hi")
    assert d.route == "owl"
    # No registry supplied — target is the raw name
    assert d.target == "hoot"


def test_empty_string_routes_to_secretary() -> None:
    d = _scan("")
    assert d.route == "owl"
    assert d.target == "secretary"


def test_whitespace_only_routes_to_secretary() -> None:
    """A message of only spaces/tabs is not a command."""
    d = _scan("   ")
    assert d.route == "owl"
    assert d.target == "secretary"


def test_panic_takes_priority_over_command() -> None:
    """/panic keyword routes panic regardless of command structure."""
    d = _scan("/panic")
    assert d.route == "panic"

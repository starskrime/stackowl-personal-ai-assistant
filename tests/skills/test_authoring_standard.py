"""D10.2 — the authoring standard, as a validator.

Measured 2026-08-05: 407 learned skills, 142 distinct base names, 265 numbered
duplicates, one lesson written twenty-one times. The standard exists to make
that impossible at the source rather than detectable afterwards, so the
load-bearing test here is the `-N` one.

Acceptance for D10.2 is "zero new non-conforming skills and no new -N families"
— judged on what gets written from now on, not on the 423-skill backlog.
"""

from __future__ import annotations

import pytest

from stackowl.skills import standard as std

_GOOD_BODY = "\n".join(f"## {s}\n\ncontent here.\n" for s in std.REQUIRED_SECTIONS)


def _rules(violations: list[std.Violation]) -> set[str]:
    return {v.rule for v in violations}


# --------------------------------------------------------------------------- #
# The rule the catalog was destroyed for want of.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name", ["foo-1", "recover_tool_search_unachieved_effect-21", "a-2", "x-999"],
)
def test_a_numbered_suffix_is_rejected(name):
    """THE POINT. Every one of the 265 duplicates had a name of this shape, and
    they existed only because the synthesizer needed a free directory."""
    assert _rules(std.validate_name(name)) == {"name"}


@pytest.mark.parametrize(
    "name", ["foo", "recover_tool_search", "web-fetch-fallback", "a1", "http2_client"],
)
def test_legitimate_names_pass(name):
    """A trailing digit is only forbidden when it is a `-N` DISAMBIGUATOR. A name
    that merely contains or ends in a digit is fine, or the rule would ban
    http2_client and a1."""
    assert std.validate_name(name) == []


# --------------------------------------------------------------------------- #
# Frontmatter.
# --------------------------------------------------------------------------- #


def test_a_long_description_is_rejected_with_the_actual_length():
    """The message has to say what was wrong AND where the detail belongs, or the
    model just truncates and loses the retrieval signal."""
    v = std.validate_frontmatter(
        {"name": "a", "when_to_use": "w", "description": "x" * 84}
    )
    assert _rules(v) == {"description"}
    assert "84 characters" in str(v[0])
    assert "when_to_use" in str(v[0])


def test_a_description_at_exactly_the_limit_passes():
    assert std.validate_frontmatter({
        "name": "a", "when_to_use": "w",
        "description": "x" * std.MAX_DESCRIPTION_CHARS,
    }) == []


def test_a_multi_sentence_description_is_rejected():
    assert "description" in _rules(std.validate_frontmatter(
        {"name": "a", "when_to_use": "w", "description": "One thing. And another."}
    ))


def test_a_trailing_full_stop_is_not_two_sentences():
    assert std.validate_frontmatter(
        {"name": "a", "when_to_use": "w", "description": "Fetch a page."}
    ) == []


def test_every_required_field_is_reported_at_once(caplog):
    """R6Q22 — all violations in one response, so the model fixes them in one
    retry instead of discovering them one call at a time."""
    v = std.validate_frontmatter({})
    assert _rules(v) == set(std.REQUIRED_FRONTMATTER)


# --------------------------------------------------------------------------- #
# Body.
# --------------------------------------------------------------------------- #


def test_a_conforming_body_passes():
    assert std.validate_body(_GOOD_BODY, known_tools=frozenset()) == []


def test_missing_sections_are_named():
    body = "## When to Use\n\nx\n"
    v = std.validate_body(body, known_tools=frozenset())
    assert "sections" in _rules(v)
    assert "Verification" in str(v[0])


def test_sections_out_of_order_are_rejected():
    reordered = list(std.REQUIRED_SECTIONS)
    reordered[0], reordered[1] = reordered[1], reordered[0]
    body = "\n".join(f"## {s}\n\nx\n" for s in reordered)
    assert "section_order" in _rules(std.validate_body(body, known_tools=frozenset()))


def test_a_missing_section_is_not_ALSO_an_ordering_error():
    """Reported once. Two errors for one mistake makes the retry message noise."""
    body = "\n".join(f"## {s}\n\nx\n" for s in std.REQUIRED_SECTIONS[:3])
    assert _rules(std.validate_body(body, known_tools=frozenset())) == {"sections"}


def test_an_unregistered_tool_name_is_rejected():
    body = _GOOD_BODY + "\nCall `web_fetchh` to get it.\n"
    v = std.validate_body(body, known_tools=frozenset({"web_fetch"}))
    assert "tool_names" in _rules(v)
    assert "web_fetchh" in str([str(x) for x in v])


def test_a_registered_tool_name_passes():
    body = _GOOD_BODY + "\nCall `web_fetch` to get it.\n"
    assert std.validate_body(body, known_tools=frozenset({"web_fetch"})) == []


def test_the_tool_rule_is_SKIPPED_when_the_registry_is_unavailable():
    """A registry that could not be consulted must never block authoring — the
    alternative is that a transient registry problem stops the agent learning."""
    body = _GOOD_BODY + "\nCall `anything_at_all` here.\n"
    assert "tool_names" not in _rules(std.validate_body(body, known_tools=None))


# --------------------------------------------------------------------------- #
# The shell-verb rule. It is STRUCTURAL, not lexical — found the hard way.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("snippet", [
    "```bash\ngrep -r foo .\n```",
    "Run `find . -name x` first.",
    "`cat a | awk 1`",
    "```\nls -la\n```",
])
def test_shell_verbs_in_code_are_rejected(snippet):
    body = _GOOD_BODY + "\n" + snippet + "\n"
    assert "shell_verbs" in _rules(std.validate_body(body, known_tools=frozenset()))


@pytest.mark.parametrize("prose", [
    "Use this to find the failing job.",
    "Read the logs to find out why.",
    "The cat sat on the mat.",
    "It also lists the results.",
])
def test_shell_verbs_in_PROSE_are_not_rejected(prose):
    """THE regression, caught in production by a live migration batch.

    The first version matched bare words anywhere and rejected "Use this to find
    the failing job" — a hardcoded English word-list applied to natural
    language, which this codebase has a standing rule against. "find", "cat" and
    "ls" are ordinary English; `find . -name x` is a shell instruction. The
    difference is structural (is it code, in command position?), not lexical.
    """
    body = _GOOD_BODY + "\n" + prose + "\n"
    assert "shell_verbs" not in _rules(std.validate_body(body, known_tools=frozenset()))


def test_a_backticked_shell_verb_reports_once_not_twice():
    """One mistake, one message. `grep` is not also an 'unregistered tool' — the
    shell_verbs message is the one that tells the author what to do instead."""
    body = _GOOD_BODY + "\n`grep -r foo .`\n"
    rules = _rules(std.validate_body(body, known_tools=frozenset({"web_fetch"})))
    assert "shell_verbs" in rules
    assert "tool_names" not in rules


def test_prose_in_backticks_is_not_mistaken_for_a_tool():
    """`foo.py` and `~/.stackowl` are not tool references. Rejecting them would
    make the rule unusable in any skill that mentions a path."""
    body = _GOOD_BODY + "\nEdit `~/.stackowl/config` and `run.py` first.\n"
    assert "tool_names" not in _rules(std.validate_body(body, known_tools=frozenset()))


def test_the_length_cap_warns_but_does_not_block():
    body = _GOOD_BODY + "\n".join(f"line {i}" for i in range(std.SOFT_MAX_LINES + 10))
    v = std.validate_body(body, known_tools=frozenset())
    assert "length" in _rules(v)
    assert std.blocking(v) == [], "a long skill is a smell, not a rejection"


# --------------------------------------------------------------------------- #
# Support files and the prompt.
# --------------------------------------------------------------------------- #


def test_only_the_three_support_dirs_are_allowed():
    assert std.validate_support_dirs(["scripts", "references", "templates"]) == []
    assert "support_dirs" in _rules(std.validate_support_dirs(["bin"]))


def test_the_prompt_text_is_generated_from_the_constants():
    """I8 — one source. If the prompt were hand-written it would teach a standard
    the validator no longer enforces, which is worse than not teaching it."""
    text = std.describe_for_prompt()
    for section in std.REQUIRED_SECTIONS:
        assert section in text
    assert str(std.MAX_DESCRIPTION_CHARS) in text
    assert str(std.SOFT_MAX_LINES) in text
    for field in std.REQUIRED_FRONTMATTER:
        assert field in text


def test_the_standard_is_versioned():
    """R6Q24 — a skill records which version it met, so a later rule change
    re-migrates only what actually moved."""
    assert isinstance(std.STANDARD_VERSION, int)
    assert std.STANDARD_VERSION >= 1

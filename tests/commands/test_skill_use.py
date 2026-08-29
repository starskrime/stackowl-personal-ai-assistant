"""D10.5 — ``/skill use <name>`` points the OPERATOR at a skill.

WHY THE VERB EXISTS. ``/skill`` had twelve verbs that MANAGE skills and not one
that USES one: ``/skill show X`` renders the body to the OPERATOR'S SCREEN and the
model never sees it. The model has had ``skill_view`` all along (guaranteed tool
base, 391 calls in 7 days) — the operator had no front door.

WHY IT STEERS RATHER THAN INJECTS. ``learn_prompt``'s module doc states the house
rule this follows: "It gathers nothing, calls no model, touches no database and
writes no skill. It returns text, and the ordinary turn does the work with the
tools it already has. That restraint is the entire design." A ``/skill use`` that
read the store and pasted the body would be a second read path beside
``skill_view``, and would make the body ``state.input_text`` — which
``turn_persist`` stages as a durable USER FACT. Steering keeps the body a TOOL
RESULT, so that whole failure mode never opens.
"""

from __future__ import annotations

from stackowl.skills.use_prompt import build_use_prompt


def test_a_bare_verb_is_not_a_skill_invocation() -> None:
    """`/skill use` with no name must not steer anything."""
    assert build_use_prompt("") is None
    assert build_use_prompt("   ") is None


def test_it_names_the_skill_and_the_tool_that_loads_it() -> None:
    prompt = build_use_prompt("deep-research")
    assert prompt is not None
    assert "deep-research" in prompt
    # skill_view is the ONE resolver. Naming it here is what keeps this a prompt
    # over the existing path rather than a second one.
    assert "skill_view" in prompt


def test_a_hyphenated_name_survives_intact() -> None:
    """The reason this is a PARAMETER and not a route token.

    `gateway/scanner.py` matches `^\\s*/(\\w+)`, and `\\w+` stops at the hyphen —
    108 of 180 live skill names contain one, so `/channel-fallback` would have
    reached a router as `channel`. As an argument the name is untouched.
    """
    prompt = build_use_prompt("channel-fallback")
    assert prompt is not None and "channel-fallback" in prompt


def test_a_qualified_name_survives_intact() -> None:
    """`skill_view` resolves `source:name`; the verb must not mangle it."""
    prompt = build_use_prompt("builtin:deep-research")
    assert prompt is not None and "builtin:deep-research" in prompt


def test_the_operators_instruction_is_carried_and_attributed() -> None:
    prompt = build_use_prompt("deep-research compare the two vendors")
    assert prompt is not None
    assert "compare the two vendors" in prompt
    assert "deep-research" in prompt


def test_it_never_begins_with_a_character_the_scanner_would_re_route() -> None:
    """I4 — the rewritten text is re-scanned before dispatch.

    `orchestrator.py`'s own comment relies on the builders prefixing a tag so the
    text cannot start with a slash. `@` matters too: two `@word` tokens route to a
    PARLIAMENT, and `/panic` anywhere trips the panic route.
    """
    for name in ("deep-research", "builtin:x", "a"):
        prompt = build_use_prompt(f"{name} @alice and @bob should see this")
        assert prompt is not None
        assert not prompt.lstrip().startswith(("/", "@")), prompt[:40]


def test_it_is_pure_and_needs_no_services() -> None:
    """Same restraint as build_learn_prompt: deterministic, no store, no network.

    If this ever needs a database read it has become a second resolver and the
    design has drifted — `skill_view` is the one that resolves names.
    """
    a = build_use_prompt("deep-research x")
    b = build_use_prompt("deep-research x")
    assert a == b


class TestTheCommandWiring:
    def test_use_is_a_declared_subcommand(self) -> None:
        """A verb absent from the meta is invisible to /help and to /find."""
        from stackowl.commands.skill_command import _SKILL_META

        names = {s.name for s in _SKILL_META.subcommands}
        assert "use" in names, f"declared verbs: {sorted(names)}"

    def test_build_turn_prompt_fires_for_use_and_ONLY_for_use(self) -> None:
        """The conditional turn prompt.

        This is the first command to return a prompt for one verb and None for the
        others. The other twelve must be byte-for-byte unaffected — a management
        verb that suddenly steered the model would be a silent behaviour change.
        """
        from stackowl.commands.skill_command import SkillCommand

        cmd = SkillCommand(store=None, loader=None, skills_root=None)

        assert cmd.build_turn_prompt("use deep-research") is not None
        for other in ("list", "show deep-research", "rm x YES", "enable x",
                      "disable x", "reload", "add ./p", "edit x", "diff x",
                      "dedupe", "migrate", "restore x", ""):
            assert cmd.build_turn_prompt(other) is None, other

    def test_a_dry_run_previews_instead_of_steering(self) -> None:
        """The hole the seam opens, and the reason it had to be closed HERE.

        `??` is intercepted in CommandRegistry.dispatch, but the turn-prompt seam
        runs BEFORE dispatch. Without an explicit check, `/skill use X ??` would
        run the skill — the exact opposite of what asking for a preview means.
        """
        from stackowl.commands.skill_command import SkillCommand

        cmd = SkillCommand(store=None, loader=None, skills_root=None)
        assert cmd.build_turn_prompt("use deep-research ??") is None
        # ...and the non-dry-run form still steers, so this is not a blanket off switch.
        assert cmd.build_turn_prompt("use deep-research") is not None

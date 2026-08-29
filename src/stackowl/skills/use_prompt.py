"""``/skill use`` — build the prompt that points the turn at one named skill.

D10.5. Sibling of :mod:`stackowl.skills.learn_prompt`, and deliberately the same
shape: ONE pure function that gathers nothing, calls no model, touches no database
and reads no skill. It returns text, and the ordinary turn does the work with the
tool it already has — ``skill_view``.

WHY THE ITEM IS NOT WHAT THE MAP SAID. D10.5's gap line called this "a direct Law 1
violation" and said "the model cannot be pointed at a skill on demand". Both were
measured false on 2026-08-29: the skill block varies in 0 of 403 lanes (while 304 of
those same lanes DO vary their prompt_hash, so the instrument works), and
``skill_view`` has been in the guaranteed tool base all along, with 391 executions in
seven days. What was genuinely missing is an OPERATOR front door: ``/skill`` shipped
twelve verbs that MANAGE skills and none that USES one, and ``/skill show`` renders a
body to the operator's screen where the model never sees it.

WHY IT STEERS INSTEAD OF PASTING THE BODY. Three reasons, in order of weight.

1. ``learn_prompt``'s restraint is the house rule: a command that fetched its own
   sources "would be a second authoring engine standing beside the nightly
   synthesizer, with a second copy of the authoring standards". Reading the skill
   store here would likewise be a second read path beside ``skill_view``.
2. It keeps the body a TOOL RESULT rather than ``state.input_text``.
   ``turn_persist`` stages ``"User: {input_text}"`` as a durable fact, and its
   machine-lane guard is a PREFIX CHECK on ``("goal-", "incident-")`` — a Telegram
   lane is not one. Pasting a 3KB body into ``input_text`` would file skill prose as
   something the user said, which is the 4,480-of-5,212 defect fixed on 2026-08-25
   reopened through a new door. Steering never opens it.
3. ``SlashCommand.build_turn_prompt`` is synchronous by contract, and resolving a
   name is an ``await``. Making the contract async to serve one verb would widen a
   seam every command implements. The tool that already resolves names is one call
   away inside the turn.

WHAT WE DEFERRED, SAID PLAINLY. The reference platform pastes the body verbatim into
a user message, which GUARANTEES the model sees it; steering means the model is told
to load the skill and could in principle not do so. That is a real trade. It is taken
because the reference pays for the guarantee twice over — it needs
``extract_user_instruction_from_skill_message`` and a set of byte-identical marker
constants purely to UNDO the expansion so its memory layer does not embed skill
bodies. We decline to make the mess rather than build the machine that cleans it.
Whether the model actually follows through is MEASURABLE, not assumable: the
observability contract in ``designs/D10.5.md`` pairs the steer with the
``skill_view`` call in the same turn, so a steer that never lands is visible.
"""

from __future__ import annotations

from stackowl.infra.observability import log

#: Prefix on every built prompt. Load-bearing for invariant I4, not decoration: the
#: gateway RE-SCANS the rewritten text, and two of the scanner's rules are unanchored
#: — two ``@word`` tokens anywhere route to a PARLIAMENT, and ``/panic`` anywhere
#: trips the panic route. A leading tag guarantees the text opens with neither a
#: slash nor an ``@``, which is the same reason ``/learn``'s builder prefixes one.
_TAG = "[/skill use]"


def build_use_prompt(args: str) -> str | None:
    """Return the instruction a normal turn runs to apply one named skill.

    ``args`` is the raw remainder after the verb — the skill name, optionally
    followed by what the operator wants done with it. Returns ``None`` when no name
    was given, which the gateway treats as "not a turn prompt" so ``/skill use`` with
    no argument falls through to the ordinary command reply instead of steering the
    model at nothing.

    Pure: deterministic for a given input, and callable with no services, no database
    and no network. That is what keeps this at ladder rung 1 — a prompt over the
    existing read path rather than machinery beside it.

    The name is passed through UNTOUCHED. It is a parameter, never a route token, so
    a hyphen survives (108 of 180 live skill names have one, and the scanner's
    ``^\\s*/(\\w+)`` would have truncated every one of them) and so does a qualified
    ``source:name``, which ``skill_view`` resolves natively.
    """
    raw = (args or "").strip()
    if not raw:
        log.skills.debug("[skills] use_prompt.build: no skill named — not a turn prompt")
        return None

    # The first token is the name; anything after it is the operator's own request.
    # ONE argument, deliberately: an `instruction=` parameter was written and cut —
    # nothing in production ever passed it, because the caller has the raw remainder
    # and this function is the thing that knows how to split it. A second way in
    # would only have let tests exercise a path production never takes.
    name, _, want = raw.partition(" ")
    want = want.strip()

    lines = [
        f'{_TAG} The user has invoked the skill "{name}" and wants you to follow it.',
        "",
        f'Load it now with skill_view(name="{name}") and then carry out its '
        "procedure. The skill body is the authority on HOW to proceed — prefer its "
        "steps over your own defaults where they differ.",
    ]
    if want:
        lines += [
            "",
            f"Alongside the skill the user asked for: {want}",
        ]
    else:
        lines += [
            "",
            "The user gave no further instruction, so apply the skill to what this "
            "conversation is already about. If that is genuinely unclear, say so and "
            "ask rather than guessing.",
        ]
    lines += [
        "",
        f'If no skill named "{name}" exists, say so plainly and use skills_list to '
        "offer the closest matches — do not silently do something else instead.",
    ]
    prompt = "\n".join(lines)
    log.skills.debug(
        "[skills] use_prompt.build: built",
        extra={"_fields": {"skill": name, "has_instruction": bool(want),
                           "chars": len(prompt)}},
    )
    return prompt

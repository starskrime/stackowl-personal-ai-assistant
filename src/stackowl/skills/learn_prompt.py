"""``/learn`` — build the prompt that turns what the user described into a skill.

D09.5. The design is ported from the reference platform; none of its code is.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT. This module contains ONE pure
function. It gathers nothing, calls no model, touches no database and writes no
skill. It returns text, and the ordinary turn does the work with the tools it
already has, finishing at ``skill_manage(action="create")``.

That restraint is the entire design. The alternative — a command that fetches the
sources and authors the skill inside its own handler — would be a second authoring
engine standing beside the nightly synthesizer, with a second copy of the authoring
standards, which is the shape Bakir's 2026-08-17 rule exists to prevent. The
capability to author already works: ``skill_audit`` records 17
``agent_self:skill_manage`` creates, the most recent at 16:29:08 on 2026-08-23.
What was missing is a named front door that carries the standards with it.

THE STANDARDS ARE REFERENCED, NEVER COPIED. :func:`standard.describe_for_prompt`
is generated from the same constants the validator enforces, so a rule cannot
change in one place and stay stale in the other. Inlining that text here would
create the second copy of a rule that this codebase's third failure shape warns
about — and it would be the *worse* copy, since only the validator's version can
reject a write.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.skills import standard

#: Used when ``/learn`` is invoked bare. The reference platform's own default, and
#: the most common real intent: the user just did something worth keeping.
_IMPLICIT_REQUEST = (
    "the workflow we just went through in this conversation — review the steps "
    "taken and distil them into a reusable skill"
)


def build_learn_prompt(user_request: str) -> str:
    """Return the instruction a normal turn runs to author one skill.

    Pure: deterministic for a given input, and callable with no services, no
    database and no network. That is what keeps ``/learn`` at ladder rung 2 —
    a prompt over the existing authoring path rather than machinery beside it.

    Args:
        user_request: free text after ``/learn`` — a description, paths, URLs,
            pasted notes, or nothing at all.
    """
    req = (user_request or "").strip()
    implicit = not req
    if implicit:
        req = _IMPLICIT_REQUEST

    prompt = (
        "[/learn] The user wants you to learn a reusable skill from the request "
        "below, and save it.\n\n"
        f"THE REQUEST:\n{req}\n\n"
        # The reference platform's sharpest observation, and the one most worth
        # porting: a request mixes SOURCES with REQUIREMENTS, and the prose after
        # a link is the user saying what they want from it. A model that fetches
        # the first URL and ignores the rest has followed the letter of the ask
        # and missed all of it.
        "The request is open-ended and may mix two kinds of content, in any "
        "order: SOURCES to gather (directories, file paths, URLs, \"what we just "
        "did\", pasted notes) AND REQUIREMENTS that shape the skill (what to "
        "focus on, what to leave out, scope, naming, the angle to take). Treat "
        "EVERY part of the request as load-bearing. Prose that follows a path or "
        "a link is NOT incidental — it is the user telling you what they want "
        "from that source. `<url> focus on the auth flow, skip the deprecated "
        "endpoints` means: gather the URL AND honour \"focus on auth, skip "
        "deprecated\" as authoring requirements. Never gather the first source "
        "and ignore the rest.\n\n"
        "Do this:\n"
        "1. Gather every source named, using the tools you already have — "
        "`read_file` and `search_files` for local files and directories, "
        "`web_fetch` for URLs, this conversation's own history if they referred "
        "to something you just did, and any pasted text as-is. If the scope is "
        "ambiguous, make a reasonable choice and say which; do not stall.\n"
        "2. Apply every requirement, focus and constraint in the request to the "
        "skill you author. These govern what the SKILL.md covers and emphasises, "
        "not merely which sources you read.\n"
        "3. Author ONE skill and save it with `skill_manage` (action=\"create\"). "
        "It requires both a name and the full SKILL.md content in the same call.\n"
        "4. Before saving, check the corpus: if a skill already covers this, "
        "improve THAT one with action=\"edit\" instead of writing a near-"
        # Not decoration. skill_manage._create's only uniqueness test is
        # `if target_dir.exists()` — a path check that a word-order permutation
        # walks straight past. On 2026-08-23 at 16:29:08 that produced
        # `instagram-video-download` beside an existing `download-instagram-video`
        # (5 runs, near-identical description). Until that gate is fixed, the
        # prompt is the only thing standing in the way.
        "duplicate. `skills_list` enumerates what exists; `skill_view <name>` "
        "reads one. A name that merely reorders the words of an existing skill "
        "IS a duplicate.\n\n"
        f"{standard.describe_for_prompt()}\n\n"
        "When you are done, tell the user the skill's name and a one-line "
        "summary of what it captured."
    )

    log.skills.info(
        "[skills] learn: prompt built",
        extra={"_fields": {"chars": len(prompt), "had_request": not implicit}},
    )
    return prompt

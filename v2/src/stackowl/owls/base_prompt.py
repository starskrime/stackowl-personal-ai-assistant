"""System prompt: a durable behavioural charter + a swappable operational adapter.

The whole point of this split is that BEHAVIOUR is the permanent invariant while
the model, the operating system, and the tool set are all swappable. So the
preamble that leads every system prompt is two layers:

  1. :func:`behavioral_charter` — WHO the assistant is and HOW it behaves, stated
     as timeless, global, high-level principles. It names no tool, no date, and
     no example domain, so it stays valid on any model, OS, or capability set.
  2. :func:`operational_adapter` — the swap-out mechanics for *today's*
     environment: the current date/time as a human-readable grounding fact, and
     the generic call PROTOCOL the model uses to invoke a capability. The live
     catalogue of actual tools is supplied separately by the provider, so the
     adapter teaches only the FORMAT — never specific tool names.

:func:`build_base_prompt` composes the two (charter first — strongest, durable
signal leads). It keeps its name so ``pipeline/steps/assemble.py`` is unchanged.
The ReAct example in the adapter is kept in lock-step with the parser in
``providers/_react.parse_react_action``.
"""

from __future__ import annotations

from datetime import datetime

from stackowl.infra.clock import now_local


def behavioral_charter() -> str:
    """The durable, global behavioural charter — principles only.

    Timeless and infrastructure-agnostic: no tool names, no date, no example
    domains. This is the assistant's permanent character statement and the part
    that must survive any change of model, platform, or capability set.
    """
    return (
        "You are an autonomous, capable agent. You take full ownership of every "
        "request and drive it to a real, delivered outcome — you are not a passive "
        "chatbot that merely answers from memory.\n\n"
        "Act over assert: prefer doing the actual work with the capabilities "
        "available to you over replying from recollection. Ground every factual "
        "claim in verified reality obtained through those capabilities, and make "
        "clear what you relied on; never present unverified or stale information "
        "as if it were current or certain.\n\n"
        "Be persistent: exhaust the capabilities available to you before "
        "concluding that something is impossible, and when one path is blocked, "
        "try another. When you lack a capability the task needs, build or learn "
        "it rather than refusing.\n\n"
        "Never decline a task by appealing to being an artificial intelligence, "
        "to a training cutoff, or to generic limitations. If, after genuine "
        "effort, you are truly blocked, say so plainly: name the specific blocker "
        "and exactly what you attempted — concisely, without apology.\n\n"
        "Communicate naturally, clearly, and honestly, in the user's own "
        "language, presenting results in the form most useful to a human."
    )


def operational_adapter(now: datetime) -> str:
    """The swappable operational layer for the current environment.

    Carries today's date/time as a human-readable grounding fact and the generic
    call protocol. Pure function: same ``now`` → same text. The ``ACTION:`` line
    and ```json fence below MUST match ``providers/_react.parse_react_action``.
    A portable strftime is used (no GNU-only ``%-d``/``%-I`` directives).
    """
    # Portable, human-readable rendering — works on Linux, macOS, and Windows.
    human_now = now.strftime("%A, %B %d, %Y at %I:%M %p %Z").strip()
    return (
        "Operational context (this changes; your character above does not).\n"
        f"Right now it is {human_now}.\n\n"
        "To use a capability, output exactly:\n"
        "ACTION: <name>\n"
        "```json\n"
        '{"<arg>": "<value>"}\n'
        "```\n"
        "Then stop and wait for the OBSERVATION (the result) before continuing. "
        "The capabilities currently available to you are listed separately; use "
        "their exact names in place of <name>."
    )


def build_base_prompt(now: datetime) -> str:
    """Compose the durable charter and the swappable adapter (charter first)."""
    return behavioral_charter() + "\n\n" + operational_adapter(now)


def build_base_prompt_now() -> str:
    """Convenience: build the base prompt using the current local time."""
    return build_base_prompt(now_local())

"""Agentic base prompt — the shared preamble that leads every system prompt.

Phase A2-A4. A weak 4B model defaults to chatbot behaviour: it announces it is
"just an AI", claims a stale training cutoff, and refuses live tasks. This
preamble flips that posture. It is prepended (strongest signal first) ahead of
the owl persona and recalled memory by ``pipeline/steps/assemble.py``.

Content, in order:
  1. Agent identity + the LIVE date (so the model never invents "2024").
  2. The tool-use mandate + the EXACT ReAct syntax the Phase A1 parser
     (``providers/_react.py``) understands, plus one parseable few-shot.
  3. Anti-excuse / persist-until-delivered posture.
  4. A one-line when-blocked escalation ladder.

``build_base_prompt`` is pure (takes ``now``, returns ``str``) so it is trivial
to test. Kept tight (~200-300 words) for the 4B context budget.
"""

from __future__ import annotations

from datetime import datetime

from stackowl.infra.clock import now_local


def build_base_prompt(now: datetime) -> str:
    """Build the agentic base preamble for the given (live) ``now``.

    Pure function: same input → same output. The literal ```json fences below
    must match ``providers/_react.parse_react_action`` exactly.
    """
    return (
        "You are an autonomous AI agent operating with real, live tools — not a "
        f"chatbot. Current date and time: {now.isoformat()}. You have live access "
        "to the web and the user's system through your tools; your built-in "
        "training knowledge is stale, so rely on tools for anything current.\n\n"
        "To use a tool, output exactly:\n"
        "ACTION: <tool_name>\n"
        "```json\n"
        '{"arg": "value"}\n'
        "```\n"
        "Then stop and wait for the OBSERVATION (the tool's result) before "
        "continuing.\n"
        "Example — to get current information:\n"
        "ACTION: web_search\n"
        "```json\n"
        '{"query": "top AI news today"}\n'
        "```\n"
        "Always TRY a tool before saying you cannot do something or lack current "
        "data.\n\n"
        "Never refuse a task with excuses about being 'just an AI', training "
        "cutoffs, or generic limitations. If a path is blocked, find another: "
        "search the web, drive the browser, run a shell command, author a "
        "reusable skill, trigger a learning pass, or build a tool. Persist across "
        "multiple tool calls until the task is actually DONE and delivered. Only "
        "stop for a genuine safety/consent gate or to ask ONE necessary "
        "clarifying question.\n\n"
        "When stuck, escalate through: web_search → browser tools → shell → "
        "skill_manage (author a skill) → reflect_now (learn) → tool_build (build "
        "a capability)."
    )


def build_base_prompt_now() -> str:
    """Convenience: build the base prompt using the current local time."""
    return build_base_prompt(now_local())

"""The agent says, BEFORE it reads a page, whether it will still need to write.

BAKIR'S DECISION, 2026-09-03, choosing what a turn may do after reading untrusted
content: **narrow on demand**. He rejected both extremes. Marker-only stops a
careless model, not a determined page. A hard narrow after any fetch is genuinely
safe and would have split 66 real turns in the last 7 days into two turns each:

    web_fetch        + write_file  35      web_fetch        + shell  24
    web_search       + write_file  31      browser_navigate + shell  23
    browser_navigate + write_file  27      browser_extract  + shell  13

WHY A DECLARATION IS NOT SECURITY THEATRE, and this is the whole reason the design
works. The declaration is made in the FETCH CALL ITSELF — before the page exists in
the context. An agent choosing ``needs_write_after=false`` is choosing it while
still uninfluenced; nothing the page says can retract it, because by the time the
page is readable the narrowing has already happened and this module refuses every
write for the rest of the turn.

WHAT IT DOES NOT DEFEND. An agent that leaves the default (true) is exactly as
exposed as before. That is the deliberate cost of not breaking those 66 turns, and
it is why the INFO lines here matter: the ratio of declared-narrow to
left-open fetches is the only evidence of whether the default is doing harm, and it
did not exist before today.

TURN-SCOPED BY CONSTRUCTION. The narrowing lives in a per-turn object owned by the
tool loop, not a contextvar and not a field on a shared registry — a turn that
narrowed itself must never narrow the next one, and the cheapest way to guarantee
that is for the state to be unreachable outside the loop that made it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from stackowl.infra.observability import log

#: The parameter a fetching tool exposes. Named for what the AGENT knows at call
#: time ("will I still need to write?"), not for the mechanism.
PARAM = "needs_write_after"

#: Tools that pull content from outside this machine into the turn.
FETCHING_TOOLS: frozenset[str] = frozenset({
    "web_fetch", "web_search", "browser_navigate", "browser_extract",
    "browser_snapshot", "browser_vision", "pdf",
})

#: Severities that may not run once a turn has narrowed itself. `read` stays —
#: the point is to stop a page causing an EFFECT, not to end the turn.
NARROWED_SEVERITIES: frozenset[str] = frozenset({"write", "consequential"})

#: What the agent is told when it tries anyway. It names the declaration, because
#: a refusal the model cannot explain to itself becomes a retry loop.
REFUSAL = (
    "Refused: earlier in this turn you called {tool} with "
    f"{PARAM}=false, which gave up write access for the rest of the turn once "
    "untrusted page content was read. This is your own declaration, not a "
    "permission error — do not retry it. Finish this turn with what you have, or "
    "start a new turn to make changes."
)

#: The schema fragment every fetching tool advertises. One definition, so a new
#: fetching tool cannot describe the same parameter differently.
PARAM_SCHEMA: dict[str, object] = {
    "type": "boolean",
    "default": True,
    "description": (
        "Set false if this turn will NOT need to write, edit, run commands or "
        "take any other action after reading this content. Doing so gives up "
        "write access for the rest of the turn and cannot be undone, which "
        "protects you from instructions hidden in the content you are about to "
        "read. Leave true (the default) if you may still need to act."
    ),
}


@dataclass
class TurnWriteNarrowing:
    """Whether this ONE turn has given up write access, and who gave it up."""

    narrowed_by: str | None = None
    #: Fetches that kept write access. Counted so the ratio is measurable.
    fetches_left_open: list[str] = field(default_factory=list)

    @property
    def is_narrowed(self) -> bool:
        return self.narrowed_by is not None

    def observe(self, tool_name: str, args: dict[str, object]) -> None:
        """Record what a fetching tool declared. Never raises.

        Reads the arg the MODEL supplied, which is the one case where trusting
        LLM-supplied input is correct: it is a request to REMOVE the agent's own
        capability, so a forged or malformed value can only make the turn safer.
        """
        if tool_name not in FETCHING_TOOLS:
            return
        if self.is_narrowed:
            # ONCE NARROWED, STAY NARROWED. Without this a later fetch would be
            # counted in `fetches_left_open`, and that counter is the only
            # evidence of how often the default is being left on.
            return
        raw = args.get(PARAM, True)
        needs_write = raw if isinstance(raw, bool) else str(raw).lower() not in ("false", "0", "no")
        if needs_write:
            self.fetches_left_open.append(tool_name)
            return
        self.narrowed_by = tool_name
        log.engine.info(
            "[narrowing] the turn gave up write access before reading content",
            extra={"_fields": {"tool": tool_name}},
        )

    def refuses(self, tool_name: str, severity: str) -> str | None:
        """The refusal text for *tool_name*, or None if it may run."""
        if not self.is_narrowed or severity not in NARROWED_SEVERITIES:
            return None
        log.engine.info(
            "[narrowing] refused a write after the turn narrowed itself",
            extra={"_fields": {
                "tool": tool_name, "severity": severity, "narrowed_by": self.narrowed_by,
            }},
        )
        return REFUSAL.format(tool=self.narrowed_by)

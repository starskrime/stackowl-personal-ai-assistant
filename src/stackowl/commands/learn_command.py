"""``/learn`` — turn what the user describes into a reusable skill.

D09.5. The command contributes a PROMPT and no machinery: it builds text via
:func:`stackowl.skills.learn_prompt.build_learn_prompt` and hands it back through
:meth:`SlashCommand.build_turn_prompt`, so the gateway runs it as the turn's input
and the ordinary pipeline does the work — finishing at ``skill_manage``.

It therefore has no :meth:`handle` body worth speaking of. ``handle`` exists only
because the ABC requires it, and it returns the same prompt text so that any
surface which has not been taught the turn-prompt seam degrades to showing the
user what WOULD have been run, rather than silently doing nothing.
"""

from __future__ import annotations

from stackowl.commands.base import SlashCommand
from stackowl.commands.metadata import CommandMeta, Example
from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState
from stackowl.skills.learn_prompt import build_learn_prompt


class LearnCommand(SlashCommand):
    """Learn a reusable skill from a description, paths, URLs, or the last turn."""

    @property
    def command(self) -> str:
        return "learn"

    @property
    def description(self) -> str:
        return "Learn a reusable skill from a description, files, URLs, or what we just did"

    @property
    def meta(self) -> CommandMeta:
        return CommandMeta(
            examples=(
                Example(invocation="/learn"),
                Example(invocation="/learn how we fixed the VPN this morning"),
                Example(invocation="/learn ./scripts/deploy.sh focus on the rollback path"),
                Example(invocation="/learn https://example.com/api skip the deprecated bits"),
            ),
        )

    def build_turn_prompt(self, args: str) -> str | None:
        """Always a turn prompt — that is the whole point of this command.

        LOGGED AT INFO, and that is the point of the line (D09.5, 2026-08-30).
        This command previously emitted NOTHING — 56 lines with no logger — while
        its own acceptance check was "the live invocation". A check whose evidence
        does not exist could never close, which is the trap D08.1 already paid for
        once. `/learn` is a PROMPT-ONLY command, so there is no downstream
        artefact that says it ran either: this line is the only possible evidence.
        """
        prompt = build_learn_prompt(args)
        log.skills.info(
            "[commands] learn: invoked — turn prompt built",
            extra={"_fields": {
                "has_args": bool(args.strip()),
                "args_chars": len(args),
                "prompt_chars": len(prompt or ""),
            }},
        )
        return prompt

    async def handle(self, args: str, state: PipelineState) -> str:
        """Fallback for a surface that does not honour the turn-prompt seam.

        Showing the instruction is a poor outcome, but it is an HONEST one: the
        user sees precisely what would have been run and can paste it. Silently
        returning "ok" would claim work that never happened, which is the failure
        this codebase's honesty rules exist to prevent.

        WARNING, not info: reaching here means a SURFACE did not honour the
        turn-prompt seam, so the user got instructions instead of a learned skill.
        That is a degraded outcome worth noticing, and without this line the two
        paths were indistinguishable in production — `/learn` would look invoked
        either way.
        """
        log.skills.warning(
            "[commands] learn: FALLBACK — this surface ignored the turn-prompt "
            "seam, so the user is being shown the instruction instead of running it",
            # getattr, not attribute access: this path exists BECAUSE the
            # surface misbehaved, and test_handle_degrades_HONESTLY passes
            # state=None on purpose to pin that. Raising here would turn "degraded
            # but honest" into "broken", which is the opposite of the point.
            extra={"_fields": {
                "has_args": bool(args.strip()),
                "session_key": getattr(state, "session_key", None),
                "channel": getattr(state, "channel", None),
            }},
        )
        return build_learn_prompt(args)

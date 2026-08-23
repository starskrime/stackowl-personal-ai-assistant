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
        """Always a turn prompt — that is the whole point of this command."""
        return build_learn_prompt(args)

    async def handle(self, args: str, state: PipelineState) -> str:
        """Fallback for a surface that does not honour the turn-prompt seam.

        Showing the instruction is a poor outcome, but it is an HONEST one: the
        user sees precisely what would have been run and can paste it. Silently
        returning "ok" would claim work that never happened, which is the failure
        this codebase's honesty rules exist to prevent.
        """
        return build_learn_prompt(args)

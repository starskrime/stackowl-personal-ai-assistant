"""OnboardingCommand — /onboarding slash command: button-driven first-run wizard.

No separate state store. Each step's prompt is a CommandResponse whose
buttons replay `/onboarding step=<name> ...` — the step name and any choice
made are encoded directly in the replayed command string, same philosophy as
every other CommandResponse action (Plan C). Every actual mutation delegates
to the already-correct existing command (`/provider add`, `/config set`,
`/connect`, `/owl create`) via the live registry instance rather than
re-implementing it here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.commands.config_helpers import config_path, load_yaml
from stackowl.commands.metadata import Arg, CommandMeta
from stackowl.commands.response import Action, CommandResponse
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.commands.registry import CommandRegistry
    from stackowl.pipeline.state import PipelineState

_ONBOARDING_META = CommandMeta(
    grammar="flag",
    group="Setup",
    args=(Arg("step", required=False, summary="internal step marker — start with bare /onboarding"),),
)


class OnboardingCommand(SlashCommand):
    """Sequences the first-run wizard: provider -> autonomy -> channels -> owl -> scheduler."""

    def __init__(self, registry: CommandRegistry | None = None) -> None:
        self._registry = registry

    @property
    def command(self) -> str:
        return "onboarding"

    @property
    def description(self) -> str:
        return "Guided first-run setup: provider, autonomy, channels, first owl, scheduler."

    @property
    def meta(self) -> CommandMeta:
        return _ONBOARDING_META

    async def handle(self, args: str, state: PipelineState) -> str | CommandResponse:
        log.gateway.debug(
            "[commands] onboarding.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        step, params = _parse_step(args)
        if step == "":
            result = self._provider_step()
        elif step == "autonomy":
            result = await self._autonomy_step(params, state)
        elif step == "channels":
            result = self._channels_step()
        elif step == "owl":
            result = self._owl_step()
        elif step == "scheduler":
            result = self._scheduler_step()
        elif step == "done":
            result = CommandResponse(text="Onboarding complete. You're all set.")
        else:
            result = CommandResponse(text=f"Unknown onboarding step: {step!r}")
        log.gateway.debug(
            "[commands] onboarding.handle: exit",
            extra={"_fields": {"step": step or "provider", "actions": len(result.actions)}},
        )
        return result

    # ------------------------------------------------------------------ steps

    def _provider_step(self) -> CommandResponse:
        path = config_path()
        data = load_yaml(path) if path.exists() else {}
        providers = data.get("providers", [])
        if providers:
            return CommandResponse(
                text=(
                    f"You already have {len(providers)} provider(s) configured. "
                    "Add another with /provider add <name> <protocol> <model> <tier> "
                    "token=<...>, or skip and continue."
                ),
                actions=(Action(label="Continue to autonomy", command="/onboarding step=autonomy"),),
            )
        return CommandResponse(
            text=(
                "Let's add your first AI provider. Type:\n"
                "  /provider add <name> <protocol> <model> <tier> token=<your-api-key>\n"
                "e.g. /provider add openai openai gpt-4o powerful token=sk-...\n"
                "Once done, tap Continue."
            ),
            actions=(Action(label="Continue to autonomy", command="/onboarding step=autonomy"),),
        )

    async def _autonomy_step(self, params: dict[str, str], state: PipelineState) -> CommandResponse:
        level = params.get("value")
        if level in ("low", "medium", "high"):
            if self._registry is not None:
                config_cmd = self._registry.get("config")
                if config_cmd is not None:
                    await config_cmd.handle(f"set autonomy_level {level}", state)
            return CommandResponse(
                text=f"Autonomy set to {level}.",
                actions=(Action(label="Continue to channels", command="/onboarding step=channels"),),
            )
        return CommandResponse(
            text="How much autonomy should owls have when invoking tools?",
            actions=(
                Action(label="Low", command="/onboarding step=autonomy value=low"),
                Action(label="Medium", command="/onboarding step=autonomy value=medium"),
                Action(label="High", command="/onboarding step=autonomy value=high"),
            ),
        )

    def _channels_step(self) -> CommandResponse:
        return CommandResponse(
            text="Connect a channel, or continue if you're set (this chat already counts).",
            actions=(
                Action(label="Connect Telegram", command="/connect telegram"),
                Action(label="Connect Slack", command="/connect slack"),
                Action(label="Connect Discord", command="/connect discord"),
                Action(label="Continue to first owl", command="/onboarding step=owl"),
            ),
        )

    def _owl_step(self) -> CommandResponse:
        return CommandResponse(
            text=(
                "Create your first owl: type /owl create <free-text description>, "
                "e.g. /owl create a calm morning-briefing assistant. Tap Continue when done."
            ),
            actions=(Action(label="Continue to scheduler", command="/onboarding step=scheduler"),),
        )

    def _scheduler_step(self) -> CommandResponse:
        return CommandResponse(
            text=(
                "Last step: check-in cadence and proactive-delivery channel. Use "
                "/config set check_in.enabled true and /config set check_in.time <HH:MM> "
                "to configure, or finish now with defaults."
            ),
            actions=(Action(label="Finish", command="/onboarding step=done"),),
        )


def _parse_step(args: str) -> tuple[str, dict[str, str]]:
    """Parse `step=<name> key=value key2=value2` into (step, params)."""
    tokens = args.strip().split()
    step = ""
    params: dict[str, str] = {}
    for token in tokens:
        if "=" not in token:
            continue
        key, _, value = token.partition("=")
        if key == "step":
            step = value
        else:
            params[key] = value
    return step, params

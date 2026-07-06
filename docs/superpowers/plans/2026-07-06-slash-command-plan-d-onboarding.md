# Slash-command Plan D: /onboarding Wizard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/onboarding`, a button-driven first-run wizard reachable mid-session (not just the pre-launch `stackowl setup` CLI wizard) — provider setup → autonomy level → channels → first owl → scheduler/notification prefs.

**Architecture:** The wizard has NO separate state store. Each step's prompt is a `CommandResponse` whose buttons replay `/onboarding step=<name> ...` with whatever choice was tapped encoded directly in the replayed command string — the same "replay is just text" philosophy Plan C's button layer already established. Every actual mutation (`/provider add`, `/config set`, `/connect`, `/owl create`) delegates to the EXISTING, already-correct command implementations — `/onboarding` itself never writes config directly, it only sequences prompts and calls those commands' own `handle()` methods.

**Tech Stack:** Python 3.13, pydantic, pytest + pytest-asyncio.

**Depends on:** Plan C (`CommandResponse`/`Action` must exist — `/onboarding` is button-driven from its first response). Plan B is not a hard dependency (provider add already worked before Plan B; Plan B only fixed its live-reload UX), but running Plan D after B means the wizard's provider step benefits from the "applied immediately" messaging instead of the old restart caveat.

## Global Constraints

- Run tests with `uv run pytest <path>` — never the full suite.
- `uv run ruff check src/` and `uv run mypy src/` clean on touched files.
- `/onboarding` never re-implements a mutation another command already owns — it calls that command's `handle()` directly (in-process, same registry instance) rather than duplicating its logic. If a step's underlying command changes, the wizard step changes with it for free.
- 4-point logging on `OnboardingCommand.handle`.
- Re-running `/onboarding` from step 1 must be safe (idempotent) — a step whose target is already configured says so and offers to skip forward, never silently blocks re-entry.

---

### Task 1: `/onboarding` skeleton — step routing + provider step

**Files:**
- Create: `src/stackowl/commands/onboarding_command.py`
- Modify: `src/stackowl/commands/assembly.py` (register)
- Modify: `src/stackowl/commands/manifest.py` (add `"onboarding"` to `SHIPPED_COMMANDS`)
- Test: new `tests/journeys/commands/test_onboarding_command.py`

**Interfaces:**
- Consumes: `stackowl.commands.response.{Action, CommandResponse}` (Plan C), `stackowl.commands.provider_command.ProviderCommand` (existing, called directly — not re-implemented), `stackowl.commands.registry.CommandRegistry` (to fetch the live `ProviderCommand`/`ConfigCommand`/etc. instances by name rather than constructing fresh ones with no DI deps).
- Produces: `OnboardingCommand.command == "onboarding"`, `handle(args: str, state: PipelineState) -> CommandResponse`. Step names (all internal, not separately exported): `""` (bare, step 1 provider), `"autonomy"`, `"channels"`, `"owl"`, `"scheduler"`, `"done"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/journeys/commands/test_onboarding_command.py
from __future__ import annotations

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.response import CommandResponse
from tests._story_6_7_helpers import make_settings, make_state


@pytest.fixture(autouse=True)
def _reset_registry():
    CommandRegistry.reset()


async def test_onboarding_bare_shows_provider_step():
    deps = CommandDeps(settings=make_settings())
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch("onboarding", "", make_state())

    assert isinstance(result, CommandResponse)
    assert "provider" in result.text.lower()
    assert len(result.actions) >= 1


async def test_onboarding_provider_already_configured_offers_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "stackowl.commands.config_helpers.config_path", lambda: tmp_path / "stackowl.yaml"
    )
    from stackowl.commands.config_helpers import save_yaml

    save_yaml(tmp_path / "stackowl.yaml", {"providers": [{"name": "acme", "protocol": "openai", "enabled": True, "default_model": "gpt-4o", "tier": "powerful"}]})
    deps = CommandDeps(settings=make_settings())
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch("onboarding", "", make_state())

    assert "already" in result.text.lower() or "skip" in result.text.lower()


async def test_onboarding_step_autonomy_shows_three_level_buttons():
    deps = CommandDeps(settings=make_settings())
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch("onboarding", "step=autonomy", make_state())

    labels = {a.label.lower() for a in result.actions}
    assert {"low", "medium", "high"} <= labels
```

(Check `CommandDeps`'s actual constructor kwargs in `assembly.py` before finalizing — it may require more than `settings=` for `register_all_commands` to succeed without error; match whatever the existing `test_memory_delete_prefix.py`-style tests already pass.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/journeys/commands/test_onboarding_command.py -v`
Expected: FAIL — `onboarding` isn't a registered command yet.

- [ ] **Step 3: Implement the skeleton + provider step**

```python
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
            return self._provider_step()
        if step == "autonomy":
            return await self._autonomy_step(params, state)
        if step == "channels":
            return self._channels_step()
        if step == "owl":
            return self._owl_step()
        if step == "scheduler":
            return self._scheduler_step()
        if step == "done":
            return CommandResponse(text="✓ Onboarding complete. You're all set.")
        return CommandResponse(text=f"Unknown onboarding step: {step!r}")

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
                    "token=<...>, or continue."
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
            assert self._registry is not None
            config_cmd = self._registry.get("config")
            if config_cmd is not None:
                await config_cmd.handle(f"set autonomy_level {level}", state)
            return CommandResponse(
                text=f"✓ Autonomy set to {level}.",
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
```

- [ ] **Step 4: Register in `assembly.py` and `manifest.py`**

In `src/stackowl/commands/assembly.py`, add (near the other DI registrations):

```python
    from stackowl.commands.onboarding_command import OnboardingCommand
    _safe_register(registry, "onboarding", lambda: OnboardingCommand(registry=registry))
```

In `src/stackowl/commands/manifest.py`, add `"onboarding",` to `SHIPPED_COMMANDS`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/journeys/commands/test_onboarding_command.py -v`
Expected: PASS.

- [ ] **Step 6: Run the reachability tests (net command count is now +1 vs Plan A's -1, back to 32)**

Run: `uv run pytest tests/journeys/commands/test_all_29_reachable.py tests/journeys/commands/test_reachability_guard.py tests/journeys/commands/test_command_manifest_drift.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/commands/onboarding_command.py src/stackowl/commands/assembly.py src/stackowl/commands/manifest.py tests/journeys/commands/test_onboarding_command.py
git commit -m "feat(commands): add /onboarding guided first-run wizard"
```

---

### Task 2: Full-plan verification

- [ ] **Step 1: Run the full onboarding + reachability test surface**

```bash
uv run pytest tests/journeys/commands/test_onboarding_command.py tests/journeys/commands/test_all_29_reachable.py tests/journeys/commands/test_reachability_guard.py tests/journeys/commands/test_command_manifest_drift.py -v
```
Expected: all PASS.

- [ ] **Step 2: Lint + type-check**

```bash
uv run ruff check src/stackowl/commands/onboarding_command.py src/stackowl/commands/assembly.py src/stackowl/commands/manifest.py
uv run mypy src/stackowl/commands/onboarding_command.py src/stackowl/commands/assembly.py src/stackowl/commands/manifest.py
```
Expected: clean on both.

- [ ] **Step 3: Manual walkthrough (documented, not automated)**

Note for the human reviewer: run `/onboarding` end to end in a live session (TUI or Telegram, after Plan C so buttons render) — bare command shows the provider step, add a provider by typing the suggested command, tap Continue through autonomy (confirm all 3 levels write correctly via `/config get autonomy_level`), channels (confirm `/connect` actually launches its real flow, not a stub), owl (confirm `/owl create` runs), scheduler (confirm the suggested `/config set check_in.*` commands work), Finish shows the completion message. Re-run `/onboarding` from scratch and confirm the provider step now says "already configured" instead of re-prompting blindly.

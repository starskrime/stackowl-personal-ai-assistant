"""TuiAssembly — factory that constructs the 5-zone Textual app + coordinator.

Mirrors :class:`MemoryAssembly` and :class:`NotificationAssembly`. The
``tui`` package owns its own assembly contract; the startup orchestrator
just calls :meth:`TuiAssembly.build` and threads the result into
:class:`CLIAdapter`.

Per the wiring plan (gleaming-finding-puppy.md, Commit D):

* Output flows EventBus → :class:`UIStateCoordinator` → Textual ``post_message``.
* Input flows :class:`ComposeArea` → :class:`ComposeSubmittedMessage`
  (Textual) → republished on EventBus as ``compose_submitted`` →
  CLIAdapter picks up via its own subscriber.
* Coordinator is NOT started here — that requires the running asyncio
  loop, which only exists inside :meth:`CLIAdapter.run`. The assembly
  returns the wired-but-inactive components; the adapter owns lifecycle.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.commands.resolver import CommandResolver
    from stackowl.commands.sequence_store import SequenceSuggestionProvider
    from stackowl.config.ui_settings import UISettings
    from stackowl.events.bus import EventBus
    from stackowl.media.stt.selector import SttSelector
    from stackowl.tui.app import StackOwlApp
    from stackowl.tui.coordinator import UIStateCoordinator
    from stackowl.tui.voice.recorder import MicRecorder
    from stackowl.tui.widgets.compose_helpers import CommandInfo


@dataclass(frozen=True)
class TuiComponents:
    """Frozen container for the wired TUI subsystem."""

    app: StackOwlApp
    coordinator: UIStateCoordinator


class TuiAssembly:
    """Factory that constructs the 5-zone Textual app + UIStateCoordinator."""

    @staticmethod
    def build(
        event_bus: EventBus,
        *,
        command_names: Iterable[str] | None = None,
        owl_names: Iterable[str] | None = None,
        command_infos: Iterable[CommandInfo] | None = None,
        ui_settings: UISettings | None = None,
        sequence_provider: SequenceSuggestionProvider | None = None,
        semantic_resolver: CommandResolver | None = None,
        recorder: MicRecorder | None = None,
        stt_selector: SttSelector | None = None,
    ) -> TuiComponents:
        """Construct the app and coordinator. Neither is started here.

        ``command_names`` and ``owl_names`` feed ``ComposeArea``'s
        autocomplete. Pass empty if not yet known — the adapter can call
        ``app.set_command_names`` later. ``sequence_provider`` /
        ``semantic_resolver`` are the (optional, off-by-default) WS-D AI lanes;
        ``None`` keeps the dropdown byte-identical to the deterministic baseline.
        """
        log.tui.info("[tui] assembly.build: entry")

        # Deferred imports keep this module cheap when TUI isn't used.
        from stackowl.tui.app import StackOwlApp
        from stackowl.tui.coordinator import UIStateCoordinator

        app = StackOwlApp(
            event_bus=event_bus,
            command_names=command_names,
            command_infos=command_infos,
            owl_names=owl_names,
            sequence_provider=sequence_provider,
            semantic_resolver=semantic_resolver,
            recorder=recorder,
            stt_selector=stt_selector,
        )
        # UIStateCoordinator's annotation is `App[object]` but Textual's
        # generic is contravariant in practice — StackOwlApp(App[None]) is
        # accepted at runtime. mypy doesn't see through the generic.
        coordinator = UIStateCoordinator(
            app=app,  # type: ignore[arg-type]
            event_bus=event_bus,
            ui_settings=ui_settings,
        )

        log.tui.info("[tui] assembly.build: exit — components wired")
        return TuiComponents(app=app, coordinator=coordinator)

"""UIStateCoordinator — bridges EventBus events to Textual messages via asyncio.Queue.

The coordinator owns a single ``asyncio.Queue`` so that any number of synchronous
EventBus callbacks can hand-off events to one async consumer that delivers them
to the Textual app on its main thread.  Sequential delivery preserves event
ordering for the user-facing UI.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

from stackowl.infra.observability import log
from stackowl.tui.color_caps import ColorCapabilityDetector, ColorTier
from stackowl.tui.coordinator_messages import build_message

if TYPE_CHECKING:
    from textual.app import App
    from textual.message import Message

    from stackowl.config.ui_settings import UISettings
    from stackowl.events.bus import EventBus

# Map EventBus event names to Textual message factories.
_EVENT_NAMES: tuple[str, ...] = (
    "pipeline_step_changed",
    "provider_degraded",
    "budget_80pct_alert",
    "job_paused",
    "parliament_started",
    "parliament_round_started",
    "parliament_round_complete",
    "synthesis_arrived",
    "parliament_session_closed",
    "memory_fact_updated",
    "evolution_batch_complete",
    "response_chunk",
    "mcp_spectator_active",
    "mcp_spectator_disconnected",
    "toast_request",
)


class UIStateCoordinator:
    """Subscribes to EventBus and pumps events → Textual messages on the main thread."""

    def __init__(
        self,
        app: "App[object]",
        event_bus: EventBus,
        *,
        ui_settings: "UISettings | None" = None,
    ) -> None:
        log.tui.debug(
            "[tui] coordinator.__init__: entry",
            extra={"_fields": {"event_names": list(_EVENT_NAMES)}},
        )
        self._app: App[object] = app
        self._event_bus = event_bus
        self._queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self._reduced_motion: bool = False
        self._consumer_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._handlers: list[tuple[str, Any]] = []
        self._color_tier: ColorTier = ColorTier.COLOR_256
        self._init_color_caps()
        self._init_reduced_motion(ui_settings)

    def _init_color_caps(self) -> None:
        """Detect terminal colour capability and store the tier."""
        log.tui.debug(
            "[tui] coordinator._init_color_caps: entry", extra={"_fields": {}}
        )
        tier = ColorCapabilityDetector().detect(dict(os.environ))
        self._color_tier = tier
        log.tui.info(
            "[tui] coordinator: color capability detected",
            extra={"_fields": {"tier": tier.value}},
        )

    def _init_reduced_motion(self, ui_settings: "UISettings | None") -> None:
        """Resolve reduced-motion preference from settings + env override."""
        log.tui.debug(
            "[tui] coordinator._init_reduced_motion: entry",
            extra={"_fields": {"has_settings": ui_settings is not None}},
        )
        env_flag: bool = os.environ.get("STACKOWL_REDUCED_MOTION", "0") == "1"
        settings_flag: bool = bool(
            ui_settings.reduced_motion if ui_settings is not None else False
        )
        self._reduced_motion = env_flag or settings_flag
        if self._reduced_motion:
            log.tui.info("[tui] motion: reduced-motion mode enabled")

    @property
    def color_tier(self) -> ColorTier:
        """Detected terminal colour-rendering capability."""
        return self._color_tier

    @property
    def reduced_motion(self) -> bool:
        """Whether reduced-motion mode is active for this coordinator."""
        return self._reduced_motion

    async def start(self) -> None:
        """Subscribe to the EventBus and start the queue consumer."""
        log.tui.debug("[tui] coordinator.start: entry", extra={"_fields": {}})
        self._loop = asyncio.get_running_loop()
        for event_name in _EVENT_NAMES:
            handler = self._make_enqueue_handler(event_name)
            self._event_bus.subscribe(event_name, handler)
            self._handlers.append((event_name, handler))
        self._consumer_task = asyncio.create_task(
            self._consume(), name="ui_state_coordinator"
        )
        log.tui.debug(
            "[tui] coordinator.start: exit",
            extra={"_fields": {"subscribed": len(self._handlers)}},
        )

    def _make_enqueue_handler(self, event_name: str) -> Any:
        """Build a synchronous callback that enqueues onto the asyncio queue."""

        def _handler(payload: Any) -> None:
            data = payload if isinstance(payload, dict) else {"payload": payload}
            try:
                if self._loop is not None and self._loop.is_running():
                    self._loop.call_soon_threadsafe(
                        self._queue.put_nowait, (event_name, data)
                    )
                else:  # fallback for tests without a running loop
                    self._queue.put_nowait((event_name, data))
            except RuntimeError as exc:
                log.tui.warning(
                    "[tui] coordinator.enqueue: dropped event",
                    exc_info=exc,
                    extra={"_fields": {"event": event_name}},
                )

        return _handler

    async def _consume(self) -> None:
        """Single consumer — delivers messages to Textual in order."""
        log.tui.debug("[tui] coordinator._consume: started", extra={"_fields": {}})
        while True:
            event_name, payload = await self._queue.get()
            try:
                await self._dispatch(event_name, payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.tui.warning(
                    "[tui] coordinator._consume: dispatch error",
                    exc_info=exc,
                    extra={"_fields": {"event": event_name}},
                )
            finally:
                self._queue.task_done()

    async def _dispatch(self, event_name: str, payload: dict[str, Any]) -> None:
        """Map event_name → Textual message and post it on the main thread."""
        message = self._build_message(event_name, payload)
        if message is None:
            log.tui.warning(
                "[tui] coordinator._dispatch: unknown event",
                extra={"_fields": {"event": event_name}},
            )
            return
        post = getattr(self._app, "call_from_thread", None)
        if callable(post):
            try:
                post(self._app.post_message, message)
            except RuntimeError as exc:
                # No running message loop (e.g. unit tests) — post directly.
                log.tui.warning(
                    "[tui] coordinator._dispatch: call_from_thread unavailable",
                    exc_info=exc,
                    extra={"_fields": {"event": event_name}},
                )
                self._app.post_message(message)
        else:
            self._app.post_message(message)

    def _build_message(
        self, event_name: str, payload: dict[str, Any]
    ) -> Message | None:
        """Delegate to :func:`coordinator_messages.build_message`."""
        return build_message(event_name, payload)

    async def stop(self) -> None:
        """Unsubscribe handlers and cancel the consumer task."""
        log.tui.debug("[tui] coordinator.stop: entry", extra={"_fields": {}})
        for event_name, handler in self._handlers:
            try:
                self._event_bus.unsubscribe(event_name, handler)
            except Exception as exc:
                log.tui.warning(
                    "[tui] coordinator.stop: unsubscribe failed",
                    exc_info=exc,
                    extra={"_fields": {"event": event_name}},
                )
        self._handlers.clear()
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                log.tui.warning(
                    "[tui] coordinator.stop: consumer cancelled (expected)",
                    extra={"_fields": {}},
                )
            except Exception as exc:
                log.tui.warning(
                    "[tui] coordinator.stop: consumer task error",
                    exc_info=exc,
                    extra={"_fields": {}},
                )
            self._consumer_task = None
        log.tui.debug("[tui] coordinator.stop: exit", extra={"_fields": {}})

"""Desktop vision routing — LOCAL-FIRST, refuse-cloud-for-desktop-frames (E12-S1).

A captured desktop frame is the most sensitive image StackOwl will ever handle:
it is the user's live screen — open email, unlocked sessions, on-screen secrets.
The adversarial review made this non-negotiable (consolidated invariant #6):

    "No desktop pixels leave the machine: SOM aux-vision routes to a
     local/self-hosted model only, else degrade/refuse."

So this router reuses the E10 vision substrate but with a HARDER policy than the
generic image path: where :class:`~stackowl.vision.selector.VisionSelector`
prefers local and *falls back* to cloud, desktop routing prefers local and
**refuses** cloud. A captured screen frame is NEVER sent to a non-local vision
backend. If no local vision backend is configured, desktop vision is
UNAVAILABLE — the frame is not described at all (the tool degrades, e.g. to
coordinate-blind/refuse, rather than leaking the screen off-box).

Non-persistable contract: a desktop frame and its routed description are flagged
non-persistable and must NEVER be logged, stored, sent to memory, written to a
pellet, or placed in a transcript — only handed to volatile model context. This
module logs only dimensions / locality / outcome, never frame bytes.

Self-healing (B5): a missing registry, no local vision, or a provider that
raises all become a structured :class:`DesktopVisionRouting` — never an
exception.
"""

from __future__ import annotations

from dataclasses import dataclass

from stackowl.infra.observability import log
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.gui.models import CaptureResult
from stackowl.vision.analyzer import analyze_image_bytes
from stackowl.vision.selector import VisionSelector

__all__ = ["DesktopVisionRouter", "DesktopVisionRouting"]

# The MIME a desktop frame is described under. Adapters capture PNG frames.
_FRAME_MIME = "image/png"

# The fixed question used to turn a frame into element descriptions / a described
# capture. Kept neutral and bounded.
_DESCRIBE_PROMPT = (
    "Describe the interactable UI elements visible on this desktop screenshot "
    "(buttons, fields, menus, links) with their visible labels and approximate "
    "screen location. Be concise."
)


@dataclass(frozen=True)
class DesktopVisionRouting:
    """The outcome of routing a desktop frame through local vision.

    Exactly one of ``description`` (routed) or ``reason`` (unavailable) is set.
    ``non_persistable`` is ALWAYS ``True`` — the description derives from a
    desktop frame and must never be logged / stored / sent to memory.
    ``routed_local`` records that routing went to an on-box backend (it is the
    only path that can be available — cloud is refused, never used).
    """

    available: bool
    description: str | None
    reason: str | None
    routed_local: bool
    non_persistable: bool = True

    @classmethod
    def routed(cls, description: str) -> DesktopVisionRouting:
        return cls(available=True, description=description, reason=None, routed_local=True)

    @classmethod
    def unavailable(cls, reason: str) -> DesktopVisionRouting:
        return cls(available=False, description=None, reason=reason, routed_local=False)


class DesktopVisionRouter:
    """Routes a captured desktop frame to a LOCAL vision backend, or refuses.

    Reuses :class:`VisionSelector` for selection but enforces the desktop-only
    policy: a non-local selection is REFUSED (the frame is never sent to cloud).
    """

    def __init__(self, registry: ProviderRegistry | None) -> None:
        self._registry = registry

    def _select_local(self) -> tuple[bool, str | None]:
        """Return (local_available, reason). Never raises (B5)."""
        if self._registry is None:
            return False, "no provider registry configured"
        try:
            selection = VisionSelector(self._registry).select()
        except Exception as exc:  # B5 — selection must not crash routing.
            log.tool.error("[gui.vision] select failed — desktop vision unavailable", exc_info=exc)
            return False, "vision selection failed"
        if not selection.available:
            return False, selection.reason or "no vision-capable model is configured"
        if not selection.is_local:
            # The frame would leave the box — REFUSE (do not route to cloud).
            log.security.warning(
                "[gui.vision] REFUSING desktop frame to CLOUD vision — local-only policy",
                extra={"_fields": {"provider": getattr(selection.provider, "name", "?")}},
            )
            return False, (
                "no LOCAL vision model is available; desktop frames are never sent "
                "to a cloud vision provider (no desktop pixels leave the machine)"
            )
        return True, None

    async def route(self, capture: CaptureResult) -> DesktopVisionRouting:
        """Route a captured frame through a local vision backend, or refuse.

        Fails closed: no local vision → ``unavailable`` (the frame is not
        described and never leaves the box). Never raises.
        """
        # 1. ENTRY — log dims/locality only, NEVER frame bytes.
        log.tool.debug(
            "[gui.vision] route: entry",
            extra={"_fields": {"width": capture.width, "height": capture.height}},
        )

        # 2. DECISION — a frame may only be routed once redaction succeeded.
        if not capture.redacted:
            log.security.warning("[gui.vision] route refused — frame not redacted")
            return DesktopVisionRouting.unavailable(
                "frame was not redacted; refusing to route an un-redacted desktop frame",
            )

        local_ok, reason = self._select_local()
        if not local_ok:
            log.tool.info(
                "[gui.vision] route: desktop vision unavailable (local-only)",
                extra={"_fields": {"reason": reason}},
            )
            return DesktopVisionRouting.unavailable(reason or "no local vision backend")

        # 3. STEP — describe via the shared analyzer (it re-selects local-first;
        # we have already proven a local backend exists, so it stays on-box).
        analysis = await analyze_image_bytes(
            self._registry,
            data=capture.frame,
            media_type=_FRAME_MIME,
            question=_DESCRIBE_PROMPT,
        )
        if not analysis.success:
            log.tool.info(
                "[gui.vision] route: analysis failed — unavailable",
                extra={"_fields": {"reason": analysis.error}},
            )
            return DesktopVisionRouting.unavailable(analysis.error or "vision analysis failed")

        # Defence in depth: if the analyzer somehow used a cloud backend, refuse
        # the result rather than surfacing an off-box description.
        if not analysis.is_local:
            log.security.warning("[gui.vision] route: analyzer used non-local backend — discarding")
            return DesktopVisionRouting.unavailable(
                "vision analysis routed off-box; discarding to keep desktop pixels local",
            )

        # 4. EXIT — never log the description text (derived from the screen).
        log.tool.debug(
            "[gui.vision] route: exit — routed to local vision",
            extra={"_fields": {"backend": analysis.backend, "non_persistable": True}},
        )
        return DesktopVisionRouting.routed(analysis.description)

"""Runtime capability manifest — a plain-language statement of what the PLATFORM
can do RIGHT NOW, derived from LIVE reachability (never a registry list).

The "registered ≠ reachable" trap: a capability can be registered yet have no
wired backend this run. So every line here is gated on the SAME runtime wiring
signal the platform itself degrades on — the ``StepServices`` field a tool reads
at execute time and falls back to "unavailable" when it is ``None``. When a
capability is genuinely unbound, its line is OMITTED (stays honest); when nothing
is reachable the manifest renders to ``""`` (byte-absent → existing prompts
unchanged).

Why this exists (TS4 / ADR-T1): the live agent invented "I can't initiate
messages" — a self-fabricated limitation reasoned from the ABSENCE of any
statement of platform capability (a flat tool list says nothing about what the
PLATFORM can do). Stating the present, MEASURED capability turns denial of a
wired capability into a contradiction the charter forbids (see
``owls.base_prompt.behavioral_charter`` — the epistemic-honesty split).

No tool names — capabilities only (charter rule).
"""

from __future__ import annotations

from dataclasses import dataclass

from stackowl.infra.observability import log
from stackowl.pipeline.services import StepServices

# Lead-in for the injected block. Frames capabilities as MEASURED facts the agent
# must not deny — the antidote to the self-invented "can't".
_HEADER = (
    "Platform capabilities live and wired for you right now (verified this turn — "
    "treat as real; never claim you cannot do these):"
)


@dataclass(frozen=True)
class CapabilityManifest:
    """What the platform can do RIGHT NOW, each flag from a live reachability probe.

    Built via :meth:`probe` from the ambient ``StepServices`` (the same wiring a
    tool reads at execute time), never from a registry/name list. ``render``
    returns the empty string when no capability is reachable, so an unwired
    platform injects nothing.
    """

    proactive_reachable: bool
    web_reachable: bool

    @classmethod
    def probe(cls, services: StepServices) -> CapabilityManifest:
        """Derive the manifest from live runtime wiring (reachability, not registry).

        - proactive: ``services.proactive_deliverer`` — the durable seam every
          proactive/scheduled delivery routes through; tools degrade to "no
          deliverer wired" when it is ``None``. This is the reachability signal
          for "can reach the user unprompted", so it is what the manifest gates on.
        - web: ``services.web_search_registry`` — ``web_search`` degrades to
          "registry not configured — unavailable" when it is ``None``.
        """
        proactive = services.proactive_deliverer is not None
        web = services.web_search_registry is not None
        log.engine.debug(
            "[capability] probe: entry",
            extra={"_fields": {"proactive": proactive, "web": web}},
        )
        return cls(proactive_reachable=proactive, web_reachable=web)

    def render(self) -> str:
        """Plain-language manifest, or ``""`` when nothing is reachable.

        Capabilities only — no tool names (charter rule). An omitted line means
        that capability is genuinely unbound this run (stays honest).
        """
        lines: list[str] = []
        if self.proactive_reachable:
            lines.append(
                "- You can act on a schedule and reach the person proactively, on "
                "your own initiative, without waiting to be prompted."
            )
        if self.web_reachable:
            lines.append(
                "- You can search the web and read current pages to ground answers "
                "in up-to-date information."
            )
        if not lines:
            log.engine.debug("[capability] render: no capabilities reachable — empty")
            return ""
        block = _HEADER + "\n" + "\n".join(lines)
        log.engine.debug(
            "[capability] render: exit",
            extra={"_fields": {"lines": len(lines), "block_len": len(block)}},
        )
        return block

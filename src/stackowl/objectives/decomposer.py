"""ObjectiveDecomposer — turn an objective intent into ordered sub-goals (1B).

This is the autonomous planner the prior audit found missing (obs 5995: "No
Autonomous Planner"). It asks the standard-tier model to break a standing
objective into a short ordered list of concrete, individually-actionable
sub-goals, each of which the driver later runs as one durable task.

Fail-safe by construction: any provider failure or empty/garbled reply degrades
to a single sub-goal that IS the whole objective, so a decomposition miss runs
the objective as one step rather than stranding it (mirrors the router's
fail-safe-to-act philosophy).
"""

from __future__ import annotations

import re
import time

from stackowl.infra.observability import log
from stackowl.objectives.model import ExpectedOutcome, SubgoalSpec
from stackowl.providers.base import Message
from stackowl.providers.registry import ProviderRegistry

#: Decomposition is a reasoning task — use the standard tier, not fast.
_DECOMP_TIER = "standard"
_DECOMP_MAX_TOKENS = 512
_DECOMP_TEMPERATURE = 0.0
#: Cap the plan size — a v1 objective is a handful of concrete steps, not a
#: sprawling tree. Anything beyond this is truncated (logged by the caller).
_MAX_SUBGOALS = 12

#: Strip a leading ordered/bullet marker: "1.", "2)", "-", "*", "•".
_MARKER_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s*")

#: A step the model declares produces a saved file: a trailing
#: ``<<produces-file>>`` or ``<<produces-file: relative/dir>>`` marker. Parsed
#: deterministically into an artifact :class:`ExpectedOutcome`; the JUDGMENT of
#: which steps produce a file is the model's (no keyword list in our code).
_PRODUCES_FILE_RE = re.compile(
    r"<<\s*produces-file\s*(?::\s*(?P<dir>[^>]*?))?\s*>>", re.IGNORECASE
)


class ObjectiveDecomposer:
    """Decompose an objective's natural-language intent into ordered sub-goals."""

    def __init__(self, provider_registry: ProviderRegistry) -> None:
        self._provider_registry = provider_registry

    def _build_prompt(self, intent: str) -> str:
        """Compose the decomposition prompt (English glue; intent inlined)."""
        return (
            "Break the following objective into a short ordered list of concrete, "
            "individually-actionable steps. Each step must be a single action the "
            "assistant can carry out on its own in one turn (fetch, read, search, "
            "summarize, compute, write, notify). Order them so each builds on the "
            "previous. Output ONLY the steps, ONE per line, with no numbering, "
            "bullets, headers, or commentary. Use at most "
            f"{_MAX_SUBGOALS} steps; prefer fewer.\n"
            "If — and ONLY if — a step is expected to SAVE or DOWNLOAD a file to "
            "disk, append the exact marker `<<produces-file>>` at the end of that "
            "step's line (or `<<produces-file: relative/dir>>` to name the target "
            "directory). Do NOT add the marker to steps that only fetch, read, "
            "search, summarize, compute, or notify without saving a file.\n\n"
            f"Objective: {intent}"
        )

    @staticmethod
    def _parse_specs(raw: str) -> list[SubgoalSpec]:
        """Parse the model reply into ordered :class:`SubgoalSpec` records.

        Strips any leading numbering/bullet marker, extracts a trailing
        ``<<produces-file[: dir]>>`` marker into an artifact acceptance criterion
        (and removes it from the description), drops blank lines, and caps at
        :data:`_MAX_SUBGOALS`. Language-neutral — no keyword/stopword lists; the
        marker convention is the model's structured signal, parsed deterministically.
        """
        specs: list[SubgoalSpec] = []
        for line in (raw or "").splitlines():
            stripped = _MARKER_RE.sub("", line)
            criterion: ExpectedOutcome | None = None
            match = _PRODUCES_FILE_RE.search(stripped)
            if match is not None:
                raw_dir = (match.group("dir") or "").strip()
                criterion = ExpectedOutcome(
                    kind="artifact", artifact_dir=raw_dir or None
                )
                stripped = _PRODUCES_FILE_RE.sub("", stripped)
            cleaned = stripped.strip()
            if cleaned:
                specs.append(
                    SubgoalSpec(description=cleaned, acceptance_criteria=criterion)
                )
            if len(specs) >= _MAX_SUBGOALS:
                break
        return specs

    @staticmethod
    def _parse_subgoals(raw: str) -> list[str]:
        """Legacy parse → ordered description strings (markers stripped).

        Retained for the ``decompose()`` contract and existing callers; delegates
        to :meth:`_parse_specs` so the two never drift.
        """
        return [s.description for s in ObjectiveDecomposer._parse_specs(raw)]

    async def decompose_specs(self, intent: str) -> list[SubgoalSpec]:
        """Return ordered sub-goal SPECS for ``intent`` (with any declared
        acceptance criteria); fail-safe to a single criterion-free spec that IS
        the whole objective, so a decomposition miss never strands it."""
        log.engine.debug(
            "[objectives] decompose: entry",
            extra={"_fields": {"intent_preview": intent[:80]}},
        )
        prompt = self._build_prompt(intent)
        messages = [Message(role="user", content=prompt)]
        t0 = time.monotonic()
        try:
            provider = self._provider_registry.get_with_cascade(_DECOMP_TIER)
            result = await provider.complete(
                messages,
                model="",
                max_tokens=_DECOMP_MAX_TOKENS,
                temperature=_DECOMP_TEMPERATURE,
            )
        except Exception as exc:  # noqa: BLE001 — never strand an objective
            log.engine.error(
                "[objectives] decompose: provider call failed — single-step fallback",
                exc_info=exc,
                extra={"_fields": {"intent_preview": intent[:80]}},
            )
            return [SubgoalSpec(description=intent)]

        specs = self._parse_specs(result.content)
        if not specs:
            log.engine.info(
                "[objectives] decompose: empty/garbled reply — single-step fallback",
                extra={"_fields": {"intent_preview": intent[:80]}},
            )
            return [SubgoalSpec(description=intent)]
        log.engine.info(
            "[objectives] decompose: exit",
            extra={
                "_fields": {
                    "intent_preview": intent[:80],
                    "subgoal_count": len(specs),
                    "with_acceptance": sum(1 for s in specs if s.acceptance_criteria),
                    "latency_ms": (time.monotonic() - t0) * 1000,
                }
            },
        )
        return specs

    async def decompose(self, intent: str) -> list[str]:
        """Return ordered sub-goal DESCRIPTIONS for ``intent`` (legacy list[str]
        contract; markers stripped). Delegates to :meth:`decompose_specs`."""
        return [s.description for s in await self.decompose_specs(intent)]

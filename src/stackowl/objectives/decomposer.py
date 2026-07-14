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

#: Task 3 (adaptive decomposition) — a trailing ``<<complexity: N>>`` marker
#: carrying the model's own 0.0-1.0 estimate of how complex that ONE step is.
#: Parsed deterministically into ``SubgoalSpec.estimated_complexity``; absent or
#: unparseable ⇒ 0.0 (see :data:`~stackowl.objectives.model.SubgoalSpec`).
_COMPLEXITY_RE = re.compile(
    r"<<\s*complexity\s*:\s*(?P<val>[0-9]*\.?[0-9]+)\s*>>", re.IGNORECASE
)

#: Task #4 — a trailing ``<<depends-on: i,j,k>>`` marker naming this step's
#: zero-based dependency indices within the SAME decomposition batch.
_DEPENDS_ON_RE = re.compile(r"<<\s*depends-on\s*:\s*(?P<idx>[0-9,\s]+)\s*>>", re.IGNORECASE)


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
            "search, summarize, compute, or notify without saving a file.\n"
            "After that (if present), ALWAYS also append `<<complexity: N>>` at "
            "the end of the line, where N is a number from 0.0 to 1.0 estimating "
            "how complex that ONE step is to carry out in a single turn: 0.0 = "
            "trivial (one simple fetch/read/notify), 1.0 = the step actually "
            "bundles several distinct actions and would benefit from being broken "
            "down further. Use the full range, not just the extremes.\n\n"
            f"Objective: {intent}"
        )

    def _build_epic_prompt(self, intent: str) -> str:
        """Graph-aware decomposition prompt (Task #4) — same base instructions
        as :meth:`_build_prompt` plus the dependency-marker convention."""
        base = self._build_prompt(intent)
        return (
            base
            + "\n\nAdditionally: if a step depends on one or more EARLIER "
            "steps in this list being done first, append "
            "`<<depends-on: i>>` (or `<<depends-on: i,j>>` for multiple) at "
            "the end of that step's line, using the 0-based position of "
            "each step it depends on. Independent steps that can run "
            "concurrently need no marker."
        )

    @staticmethod
    def _parse_specs(raw: str) -> list[SubgoalSpec]:
        """Parse the model reply into ordered :class:`SubgoalSpec` records.

        Strips any leading numbering/bullet marker, extracts a trailing
        ``<<produces-file[: dir]>>`` marker into an artifact acceptance criterion
        and a trailing ``<<complexity: N>>`` marker into
        :attr:`SubgoalSpec.estimated_complexity` (Task 3; removing both from the
        description), drops blank lines, and caps at :data:`_MAX_SUBGOALS`.
        Language-neutral — no keyword/stopword lists; the marker convention is the
        model's structured signal, parsed deterministically.
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
            complexity = 0.0
            cmatch = _COMPLEXITY_RE.search(stripped)
            if cmatch is not None:
                try:
                    complexity = float(cmatch.group("val"))
                except ValueError:
                    complexity = 0.0
                complexity = max(0.0, min(1.0, complexity))
                stripped = _COMPLEXITY_RE.sub("", stripped)
            cleaned = stripped.strip()
            if cleaned:
                specs.append(
                    SubgoalSpec(
                        description=cleaned,
                        acceptance_criteria=criterion,
                        estimated_complexity=complexity,
                    )
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

    @staticmethod
    def _parse_depends_on(line: str) -> tuple[str, list[int]]:
        """Extract a trailing ``<<depends-on: ...>>`` marker; returns
        (line with marker removed, parsed indices — invalid/unparseable
        tokens are dropped, never raise)."""
        match = _DEPENDS_ON_RE.search(line)
        if match is None:
            return line, []
        indices: list[int] = []
        for token in match.group("idx").split(","):
            token = token.strip()
            if token.isdigit():
                indices.append(int(token))
        return _DEPENDS_ON_RE.sub("", line), indices

    async def decompose_epic_specs(self, intent: str) -> list[SubgoalSpec]:
        """Graph-aware decomposition (Task #4): same fail-safe contract as
        :meth:`decompose_specs` (provider failure / empty reply degrades to a
        single dependency-free spec), but parses ``<<depends-on: ...>>``
        markers into :attr:`SubgoalSpec.depends_on`. Does not call or modify
        :meth:`decompose_specs` — kept fully separate so the plain-objective
        path is untouched."""
        log.engine.debug(
            "[objectives] decompose_epic: entry",
            extra={"_fields": {"intent_preview": intent[:80]}},
        )
        prompt = self._build_epic_prompt(intent)
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
        except Exception as exc:  # noqa: BLE001 — never strand an epic
            log.engine.error(
                "[objectives] decompose_epic: provider call failed — single-step fallback",
                exc_info=exc,
                extra={"_fields": {"intent_preview": intent[:80]}},
            )
            return [SubgoalSpec(description=intent)]

        specs: list[SubgoalSpec] = []
        for line in (result.content or "").splitlines():
            stripped = _MARKER_RE.sub("", line)
            stripped, depends_on = self._parse_depends_on(stripped)
            criterion: ExpectedOutcome | None = None
            match = _PRODUCES_FILE_RE.search(stripped)
            if match is not None:
                raw_dir = (match.group("dir") or "").strip()
                criterion = ExpectedOutcome(kind="artifact", artifact_dir=raw_dir or None)
                stripped = _PRODUCES_FILE_RE.sub("", stripped)
            complexity = 0.0
            cmatch = _COMPLEXITY_RE.search(stripped)
            if cmatch is not None:
                try:
                    complexity = max(0.0, min(1.0, float(cmatch.group("val"))))
                except ValueError:
                    complexity = 0.0
                stripped = _COMPLEXITY_RE.sub("", stripped)
            cleaned = stripped.strip()
            if cleaned:
                specs.append(
                    SubgoalSpec(
                        description=cleaned,
                        acceptance_criteria=criterion,
                        estimated_complexity=complexity,
                        depends_on=depends_on,
                    )
                )
            if len(specs) >= _MAX_SUBGOALS:
                break

        if not specs:
            log.engine.info(
                "[objectives] decompose_epic: empty/garbled reply — single-step fallback",
                extra={"_fields": {"intent_preview": intent[:80]}},
            )
            return [SubgoalSpec(description=intent)]
        log.engine.info(
            "[objectives] decompose_epic: exit",
            extra={
                "_fields": {
                    "intent_preview": intent[:80],
                    "subgoal_count": len(specs),
                    "with_deps": sum(1 for s in specs if s.depends_on),
                    "latency_ms": (time.monotonic() - t0) * 1000,
                }
            },
        )
        return specs

    async def decompose(self, intent: str) -> list[str]:
        """Return ordered sub-goal DESCRIPTIONS for ``intent`` (legacy list[str]
        contract; markers stripped). Delegates to :meth:`decompose_specs`."""
        return [s.description for s in await self.decompose_specs(intent)]

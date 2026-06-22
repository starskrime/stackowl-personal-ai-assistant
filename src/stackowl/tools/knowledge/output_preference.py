"""set_output_preference — persist a durable, channel-wide output-format preference.

The deterministic delivery seam (:func:`stackowl.channels._format.apply_output_preferences`,
invoked by ``pipeline.steps.deliver._enforce_output_prefs``) READS a stored
``output_tables`` preference and, when it is ``off``, rewrites tables to a plain
list on EVERY reply. But nothing ever WROTE that preference — so a recalled "no
tables" preference lived only as unstructured memory and was never enforced.

This tool is the LLM-driven write half of that loop. The model calls it whenever
the user states how they want responses formatted (in any language — the model,
not a hardcoded keyword list, decides intent and supplies the canonical value).
The write is GLOBAL (``GLOBAL_OWNER_KEY``): one preference honored across all
channels for the single principal. Enforcement is deterministic post-write, so a
single recording suffices regardless of model strength or channel.

Severity: ``write`` — a persisted preference is a write+audit mutation, but it is
low-blast-radius and reversible (set ``value='on'`` to undo), NOT consequential.
``toolset_group="knowledge"`` — it lives alongside ``memory`` in the read/knowledge
group; not consent-gated.
"""

from __future__ import annotations

import time

from stackowl.channels._format import _OFF_VALUES  # canonical off-values (no drift)
from stackowl.infra.observability import log
from stackowl.memory.preferences import GLOBAL_OWNER_KEY
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult

# Keys this tool may persist. An allowlist (not free-form) so the model can only
# set preferences the enforcement seam actually understands. Extend as new
# enforced output preferences are added.
_ALLOWED_KEYS: frozenset[str] = frozenset({"output_tables"})

# Accepted canonical values: any off-value (suppress) plus "on" (re-enable). The
# MODEL maps the user's natural phrasing (any language) to one of these — we keep
# NO natural-language word-list here ([[feedback_no_hardcoded_keyword_lists]]).
_ALLOWED_VALUES: frozenset[str] = _OFF_VALUES | {"on"}


class SetOutputPreferenceTool(Tool):
    """Persist a durable, channel-wide output-format preference (enforced on every reply)."""

    @property
    def name(self) -> str:
        return "set_output_preference"

    @property
    def description(self) -> str:
        return (
            "Persist a durable output-format preference that is then ENFORCED on "
            "EVERY future reply, on every channel, deterministically (not a hint "
            "the model may ignore). Call this whenever the user expresses how they "
            "want responses formatted — e.g. asks to stop/avoid tables, or to "
            "allow them again — in ANY language. "
            "key='output_tables' with value='off' suppresses markdown tables "
            "(they render as plain lists everywhere); value='on' allows them "
            "again. "
            "LANE: a standing FORMAT rule for all output. "
            "ANTI-LANE: do NOT use 'memory' for this — a recalled note is not "
            "enforced; this tool is."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "enum": sorted(_ALLOWED_KEYS),
                    "description": "Which output preference to set. 'output_tables' "
                    "controls whether markdown tables are allowed.",
                },
                "value": {
                    "type": "string",
                    "enum": sorted(_ALLOWED_VALUES),
                    "description": "'off' (or false/no/0/none/disabled) to suppress; "
                    "'on' to allow.",
                },
            },
            "required": ["key", "value"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
            commit_coupling="transactional",
            toolset_group="knowledge",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        key = str(kwargs.get("key", "")).strip().casefold()
        value = str(kwargs.get("value", "")).strip().casefold()
        # 1. ENTRY
        log.tool.info(
            "set_output_preference.execute: entry",
            extra={"_fields": {"key": key, "value": value}},
        )

        # Hard-validate args — a bad key/value is a pre-exec refusal that wrote
        # nothing, so it must NOT count as an effectful failure (give-up floor).
        if key not in _ALLOWED_KEYS:
            return self._refuse(
                f"Unknown preference key {key!r}. Settable keys: "
                f"{', '.join(sorted(_ALLOWED_KEYS))}.",
                t0,
            )
        if value not in _ALLOWED_VALUES:
            return self._refuse(
                f"Unknown value {value!r} for {key!r}. Use 'off' (suppress) or "
                "'on' (allow).",
                t0,
            )

        store = get_services().preference_store
        if store is None:
            return self._unavailable("no preference store is configured", t0)

        # 2. DECISION / 3. STEP — persist GLOBALLY so enforcement spans all channels.
        try:
            await store.set(GLOBAL_OWNER_KEY, key, value)
        except Exception as exc:  # B5 / self-healing — degrade, never raise.
            log.tool.error(
                "set_output_preference.execute: write failed — degrading",
                exc_info=exc, extra={"_fields": {"key": key, "value": value}},
            )
            return self._unavailable(f"{type(exc).__name__}: {exc}", t0)

        # 4. EXIT — mutating turns must be visible.
        if value in _OFF_VALUES and key == "output_tables":
            msg = "Output preference saved: tables are now OFF on every channel — replies will use plain lists."
        elif key == "output_tables":
            msg = "Output preference saved: tables are now ON on every channel."
        else:  # future keys
            msg = f"Output preference saved: {key} = {value} (applies to every channel)."
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "set_output_preference.execute: exit",
            extra={"_fields": {"success": True, "key": key, "value": value,
                               "duration_ms": duration_ms}},
        )
        return ToolResult(success=True, output=msg, duration_ms=duration_ms)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _refuse(msg: str, t0: float) -> ToolResult:
        """Pre-execution refusal (bad args) — nothing was written."""
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "set_output_preference.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(
            success=False, output="", error=msg, duration_ms=duration_ms,
            side_effect_committed=False,
        )

    @staticmethod
    def _unavailable(reason: str, t0: float) -> ToolResult:
        """Store down/missing — degrade structurally; the write never landed."""
        msg = f"preference store unavailable: {reason}"
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.warning(
            "set_output_preference.execute: store unavailable — structured degradation",
            extra={"_fields": {"reason": reason, "duration_ms": duration_ms}},
        )
        return ToolResult(
            success=False, output="", error=msg, duration_ms=duration_ms,
            side_effect_committed=False,
        )

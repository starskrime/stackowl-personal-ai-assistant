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

import json
import time
from typing import get_args

from stackowl.channels._format import (  # canonical off-values + style vocabulary (no drift)
    _OFF_VALUES,
    OUTPUT_STYLE_FIELDS,
    OUTPUT_STYLE_KEY,
    OutputStyle,
)
from stackowl.infra.observability import log
from stackowl.memory.preferences import GLOBAL_OWNER_KEY
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult

# Legacy single key — on/off table suppression. Subsumed by output_style.tables
# but kept settable for byte-compatibility with anything already using it.
_LEGACY_TABLES_KEY = "output_tables"

# Keys this tool may persist: the legacy table key, the whole structured record
# (output_style, value = a JSON object), and each individual style field. An
# allowlist (not free-form) so the model can only set preferences the enforcement
# seam can mechanically enforce (the key-admissibility rule).
_ALLOWED_KEYS: frozenset[str] = (
    frozenset({_LEGACY_TABLES_KEY, OUTPUT_STYLE_KEY}) | OUTPUT_STYLE_FIELDS
)

# Accepted canonical values for the legacy key: any off-value (suppress) plus
# "on" (re-enable). The MODEL maps the user's natural phrasing (any language) to
# one of these — NO natural-language word-list here ([[feedback_no_hardcoded_keyword_lists]]).
_ALLOWED_VALUES: frozenset[str] = _OFF_VALUES | {"on"}


def _field_vocab() -> dict[str, tuple[str, ...]]:
    """Allowed values per style field, derived from the model (no drift)."""
    return {
        name: get_args(field.annotation)
        for name, field in OutputStyle.model_fields.items()
    }


class SetOutputPreferenceTool(Tool):
    """Persist a durable, channel-wide output-format preference (enforced on every reply)."""

    @property
    def name(self) -> str:
        return "set_output_preference"

    @property
    def description(self) -> str:
        vocab = ", ".join(
            f"{name}={'|'.join(vals)}" for name, vals in _field_vocab().items()
        )
        return (
            "Persist a durable output-format preference that is then ENFORCED on "
            "EVERY future reply, on every channel, deterministically (not a hint "
            "the model may ignore). Call this whenever the user expresses how they "
            "want responses formatted — in ANY language. "
            "Set one style field at a time: key=<field>, value=<value>, where "
            f"fields are: {vocab}. "
            "(markdown=minimal strips asterisk bold/italics; links=titles renders "
            "each link as a titled tappable hyperlink; length=terse keeps replies "
            "short.) "
            "Legacy key='output_tables' (value 'on'/'off') still works and maps to "
            "the 'tables' field. "
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
                    "description": "Which output preference to set: a style field "
                    "(markdown/links/tables/emoji/length), the legacy "
                    "'output_tables', or 'output_style' to set several fields at "
                    "once (value = a JSON object).",
                },
                "value": {
                    "type": "string",
                    "description": "The canonical value for the chosen key — e.g. "
                    "'minimal' for markdown, 'titles' for links, 'off' for "
                    "output_tables. For key='output_style', a JSON object like "
                    '{"markdown":"minimal","links":"titles"}.',
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

        store = get_services().preference_store
        if store is None:
            return self._unavailable("no preference store is configured", t0)

        # 2. DECISION — legacy single key vs the structured output_style record.
        if key == _LEGACY_TABLES_KEY:
            return await self._set_legacy_tables(store, value, t0)
        return await self._set_style(store, key, value, t0)

    # ------------------------------------------------------------- write paths

    async def _set_legacy_tables(
        self, store: object, value: str, t0: float,
    ) -> ToolResult:
        """Byte-compatible ``output_tables`` write (on/off) — unchanged behavior."""
        if value not in _ALLOWED_VALUES:
            return self._refuse(
                f"Unknown value {value!r} for 'output_tables'. Use 'off' "
                "(suppress) or 'on' (allow).",
                t0,
            )
        try:
            await store.set(GLOBAL_OWNER_KEY, _LEGACY_TABLES_KEY, value)  # type: ignore[attr-defined]
        except Exception as exc:  # B5 / self-healing — degrade, never raise.
            log.tool.error(
                "set_output_preference.execute: write failed — degrading",
                exc_info=exc, extra={"_fields": {"key": _LEGACY_TABLES_KEY, "value": value}},
            )
            return self._unavailable(f"{type(exc).__name__}: {exc}", t0)
        if value in _OFF_VALUES:
            msg = "Output preference saved: tables are now OFF on every channel — replies will use plain lists."
        else:
            msg = "Output preference saved: tables are now ON on every channel."
        return self._ok(msg, _LEGACY_TABLES_KEY, value, t0)

    async def _set_style(
        self, store: object, key: str, value: str, t0: float,
    ) -> ToolResult:
        """Validate against the closed style vocabulary, then merge+persist the
        structured ``output_style`` record (only explicitly-set fields, JSON)."""
        # Build the patch: a single field, or a JSON object for the whole record.
        if key == OUTPUT_STYLE_KEY:
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError) as exc:
                return self._refuse(f"output_style must be a JSON object: {exc}", t0)
            if not isinstance(parsed, dict):
                return self._refuse("output_style must be a JSON object.", t0)
            patch = {k: (v.casefold() if isinstance(v, str) else v) for k, v in parsed.items()}
        else:  # a single style field
            patch = {key: value}

        # Read the existing record (explicit fields only) and merge the patch.
        try:
            existing_raw = await store.get(GLOBAL_OWNER_KEY, OUTPUT_STYLE_KEY)  # type: ignore[attr-defined]
        except Exception as exc:  # B5 — degrade, never raise.
            log.tool.error(
                "set_output_preference.execute: style read failed — degrading",
                exc_info=exc, extra={"_fields": {"key": key}},
            )
            return self._unavailable(f"{type(exc).__name__}: {exc}", t0)
        existing: dict[str, object] = {}
        if existing_raw:
            try:
                loaded = json.loads(existing_raw)
                if isinstance(loaded, dict):
                    existing = {k: v for k, v in loaded.items() if k in OUTPUT_STYLE_FIELDS}
            except (ValueError, TypeError):
                existing = {}  # corrupt prior record — overwrite cleanly
        merged = {**existing, **patch}

        # Inline validation against the closed vocabulary (extra=forbid rejects
        # unknown keys; Literal fields reject unknown values). Nothing written yet.
        try:
            OutputStyle.model_validate(merged)
        except Exception as exc:
            return self._refuse(
                f"Invalid output style {patch!r}: {self._first_error(exc)}", t0,
            )

        # 3. STEP — persist GLOBALLY so enforcement spans all channels.
        try:
            await store.set(GLOBAL_OWNER_KEY, OUTPUT_STYLE_KEY, json.dumps(merged))  # type: ignore[attr-defined]
        except Exception as exc:  # B5 / self-healing — degrade, never raise.
            log.tool.error(
                "set_output_preference.execute: style write failed — degrading",
                exc_info=exc, extra={"_fields": {"key": key}},
            )
            return self._unavailable(f"{type(exc).__name__}: {exc}", t0)

        applied = ", ".join(f"{k}={v}" for k, v in patch.items())
        msg = f"Output style saved: {applied} (applies to every channel)."
        return self._ok(msg, key, value, t0)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _first_error(exc: Exception) -> str:
        """A short, honest reason from a pydantic ValidationError (or any error)."""
        errors = getattr(exc, "errors", None)
        if callable(errors):
            try:
                items = errors()
                if items:
                    loc = ".".join(str(p) for p in items[0].get("loc", ()))
                    return f"{loc}: {items[0].get('msg', 'invalid')}".strip(": ")
            except Exception:  # pragma: no cover - defensive
                pass
        return str(exc)

    def _ok(self, msg: str, key: str, value: str, t0: float) -> ToolResult:
        """4. EXIT — mutating turns must be visible."""
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "set_output_preference.execute: exit",
            extra={"_fields": {"success": True, "key": key, "value": value,
                               "duration_ms": duration_ms}},
        )
        return ToolResult(success=True, output=msg, duration_ms=duration_ms)

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

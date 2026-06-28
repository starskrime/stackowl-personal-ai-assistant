"""Shared, channel-agnostic markdown normalisation.

GFM pipe-tables are not representable in Telegram MarkdownV2 or Slack mrkdwn;
left alone their ``|``/``-`` chars render as broken escaped text. We flatten a
detected table into a fenced, column-aligned block, which BOTH channel
converters pass through verbatim via their code-fence stash phase. Detection
anchors on a header row immediately followed by a delimiter row (cells of only
``-``/``:``/spaces/pipes) — a lone ``-`` or a horizontal rule is never a table.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids import cycle
    from stackowl.memory.preferences import PreferenceStore

_DELIM_CELL = re.compile(r"^\s*:?-{1,}:?\s*$")


def _is_table_row(line: str) -> bool:
    return line.strip().startswith("|")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_delimiter_row(line: str) -> bool:
    if "|" not in line:
        return False
    cells = _cells(line)
    return len(cells) >= 1 and all(_DELIM_CELL.match(c) for c in cells)


def flatten_gfm_tables(text: str) -> str:
    """Flatten GFM pipe-tables to fenced, column-aligned text blocks.

    Both Telegram MarkdownV2 and Slack mrkdwn stash ````` ``` ````` fences
    verbatim before their escape/conversion passes, so the rendered block
    arrives at the user unchanged. A lone ``-`` list bullet or a ``---``
    horizontal rule is never mistaken for a table because detection requires
    the header+delimiter LINE PAIR: a row immediately followed by a row whose
    cells consist only of ``-``, ``:`` and spaces.

    Args:
        text: Raw assistant GFM text, possibly containing one or more tables.

    Returns:
        Text with any detected GFM tables replaced by fenced aligned blocks;
        all other content is returned byte-for-byte unchanged.
    """
    return _transform_tables(text, _render_block)


def _transform_tables(
    text: str, render: Callable[[list[str], list[list[str]]], str],
) -> str:
    """Detect GFM pipe-tables (fence-aware) and replace each with ``render(header,
    body)``. Shared by :func:`flatten_gfm_tables` (fenced block) and
    :func:`tables_to_plain_list` (plain list) so detection can never drift."""
    if "|" not in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    in_fence = False
    while i < n:
        # Track fenced code blocks (``` or ~~~); skip table detection inside them.
        stripped = lines[i].strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            out.append(lines[i])
            i += 1
            continue
        if in_fence:
            out.append(lines[i])
            i += 1
            continue
        # A table = header row, then a delimiter row, then >=0 body rows.
        if (
            i + 1 < n
            and _is_table_row(lines[i])
            and not _is_delimiter_row(lines[i])
            and _is_table_row(lines[i + 1])
            and _is_delimiter_row(lines[i + 1])
        ):
            header = _cells(lines[i])
            body: list[list[str]] = []
            j = i + 2
            while j < n and _is_table_row(lines[j]) and not _is_delimiter_row(lines[j]):
                body.append(_cells(lines[j]))
                j += 1
            out.append(render(header, body))
            i = j
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


# Preference values (casefolded) that DISABLE a feature. Boolean parsing, not an
# NL word-list — multilingual/learned phrasing maps to a canonical key elsewhere.
_OFF_VALUES = frozenset({"off", "false", "no", "0", "none", "disabled"})


def _render_plain_list(header: list[str], body: list[list[str]]) -> str:
    """Render a table as a plain bullet list — no pipes, no fence, no dashes."""
    if not body:
        return "- " + ", ".join(h for h in header if h)
    out_lines: list[str] = []
    for row in body:
        pairs: list[str] = []
        for idx, cell in enumerate(row):
            head = header[idx] if idx < len(header) else ""
            pairs.append(f"{head}: {cell}" if head else cell)
        out_lines.append("- " + "; ".join(pairs))
    return "\n".join(out_lines)


def tables_to_plain_list(text: str) -> str:
    """Convert GFM pipe-tables into a plain bullet list (table form removed).

    Stronger than :func:`flatten_gfm_tables` (which keeps a fenced, aligned
    block): this honors a stored "no tables" preference by removing the table
    representation entirely. Non-table content is returned unchanged.
    """
    return _transform_tables(text, _render_plain_list)


def apply_output_preferences(text: str, prefs: Mapping[str, str]) -> str:
    """Deterministically ENFORCE an owner's stored output-format preferences.

    Channel-agnostic, applied at the delivery seam so a recalled preference
    becomes an enforced constraint (not a hint the model may ignore). Currently
    honors the canonical ``output_tables`` key: a value in :data:`_OFF_VALUES`
    converts tables to a plain list. No matching preference → text unchanged
    (byte-identical baseline).
    """
    tables = prefs.get("output_tables")
    if tables is not None and tables.strip().casefold() in _OFF_VALUES:
        return tables_to_plain_list(text)
    return text


# --------------------------------------------------------------------------- #
# Structured output style (LS1)                                               #
# --------------------------------------------------------------------------- #
# One durable preference key holding a small CLOSED record of DELIVERY TRANSFORMS.
# Each field is something the delivery seam can mechanically enforce (the
# key-admissibility rule) — free-form style desires route to the owl charter,
# not here. LS2 enforces these in ``_enforce_output_prefs``; LS1 is storage +
# vocabulary + this read helper only. Persisted as a JSON object of *explicitly
# set* fields under ``OUTPUT_STYLE_KEY`` (so the ``output_tables`` alias can fill
# the ``tables`` field only when the style did not set it).
OUTPUT_STYLE_KEY = "output_style"


class OutputStyle(BaseModel):
    """A closed vocabulary of independently-enforceable delivery transforms.

    Every field is optional with a no-op default, so a partial style (one field
    set) is valid and an all-default record changes nothing. ``tables`` subsumes
    the legacy ``output_tables`` key — see :func:`resolve_output_style`.
    """

    markdown: Literal["off", "minimal", "full"] = "full"
    links: Literal["inline", "titles"] = "inline"
    tables: Literal["on", "off"] = "on"
    emoji: Literal["on", "off"] = "on"
    length: Literal["terse", "normal"] = "normal"

    model_config = ConfigDict(extra="forbid")


# Field names of the style record — derived from the model (no hardcoded list to
# drift) so callers can tell a style sub-field from another preference key.
OUTPUT_STYLE_FIELDS: frozenset[str] = frozenset(OutputStyle.model_fields)


def resolve_output_style(prefs: Mapping[str, str]) -> OutputStyle:
    """Resolve the effective :class:`OutputStyle` from a merged preference map.

    ``prefs`` is an already-merged ``{key: value}`` map (e.g. global UNDER
    channel). Reads the JSON under ``OUTPUT_STYLE_KEY`` (only the fields actually
    set), then back-fills ``tables`` from the legacy ``output_tables`` key when
    the style itself did not set ``tables`` (so anything still writing
    ``output_tables`` keeps working, read through as ``tables``). A missing or
    corrupt record degrades to an all-default (no-op) style — never raises.
    """
    raw: dict[str, object] = {}
    encoded = prefs.get(OUTPUT_STYLE_KEY)
    if encoded:
        try:
            parsed = json.loads(encoded)
            if isinstance(parsed, dict):
                raw = {k: v for k, v in parsed.items() if k in OUTPUT_STYLE_FIELDS}
        except (ValueError, TypeError) as exc:  # corrupt store — degrade loudly
            log.gateway.warning(
                "[format] resolve_output_style: corrupt output_style — using defaults",
                extra={"_fields": {"error": str(exc)}},
            )
    # Back-compat alias: output_tables=off ⇒ tables=off, unless style set tables.
    if "tables" not in raw:
        legacy = prefs.get("output_tables")
        if legacy is not None and legacy.strip().casefold() in _OFF_VALUES:
            raw["tables"] = "off"
    try:
        return OutputStyle.model_validate(raw)
    except Exception as exc:  # value out of vocabulary in store — degrade loudly
        log.gateway.warning(
            "[format] resolve_output_style: invalid stored style — using defaults",
            extra={"_fields": {"error": str(exc)}},
        )
        return OutputStyle()


async def load_output_style(store: PreferenceStore, owner_key: str) -> OutputStyle:
    """Read the resolved :class:`OutputStyle` for ``owner_key``, merging scopes.

    Merges the cross-channel GLOBAL prefs UNDER the per-channel ``owner_key``
    prefs (channel overrides global), mirroring the delivery seam, then resolves.
    The consumer for LS2 enforcement and the LS5 ``/style`` command.
    """
    from stackowl.memory.preferences import GLOBAL_OWNER_KEY

    global_prefs = await store.list_for_owner(GLOBAL_OWNER_KEY)
    owner_prefs = await store.list_for_owner(owner_key)
    return resolve_output_style({**global_prefs, **owner_prefs})


def _render_block(header: list[str], body: list[list[str]]) -> str:
    rows = [header, *body]
    width = max(len(r) for r in rows)
    norm = [r + [""] * (width - len(r)) for r in rows]
    cols = [max(len(norm[r][c]) for r in range(len(norm))) for c in range(width)]

    def fmt(r: list[str]) -> str:
        return "  ".join(r[c].ljust(cols[c]) for c in range(width)).rstrip()

    lines = [fmt(norm[0]), "  ".join("-" * cols[c] for c in range(width)).rstrip()]
    lines += [fmt(r) for r in norm[1:]]
    return "```\n" + "\n".join(lines) + "\n```"

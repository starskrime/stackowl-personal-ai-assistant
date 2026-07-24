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
from urllib.parse import urlparse

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
    """Flatten GFM pipe-tables to a fenced, column-aligned block — or, when that
    block would be too wide for a mobile screen, to the same plain per-row list
    :func:`tables_to_plain_list` uses (see :func:`_render_auto`).

    Both Telegram MarkdownV2 and Slack mrkdwn stash ````` ``` ````` fences
    verbatim before their escape/conversion passes, so the rendered block
    arrives at the user unchanged. A lone ``-`` list bullet or a ``---``
    horizontal rule is never mistaken for a table because detection requires
    the header+delimiter LINE PAIR: a row immediately followed by a row whose
    cells consist only of ``-``, ``:`` and spaces.

    Args:
        text: Raw assistant GFM text, possibly containing one or more tables.

    Returns:
        Text with any detected GFM tables replaced by an aligned block or a
        plain list (whichever fits); all other content is returned
        byte-for-byte unchanged.
    """
    return _transform_tables(text, _render_auto)


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
    becomes an enforced constraint (not a hint the model may ignore). Resolves
    the structured :class:`OutputStyle` (which subsumes the legacy
    ``output_tables`` key) and applies every enforceable transform + a
    post-transform verifier (LS2). An all-default style (no preference set) is a
    byte-identical no-op.
    """
    return resolve_output_style(prefs).enforce(text)


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

    # --- LS2 deterministic enforcement (the load-bearing 20%) --------------- #
    # Each ``_enforce_*`` is the mechanical transform for ONE field; every one is
    # idempotent (applying twice == once) and code-fence-safe where it matters, so
    # they double as both the apply pass and the verifier's repair pass.

    def _enforce_tables(self, text: str) -> str:
        return tables_to_plain_list(text) if self.tables == "off" else text

    def _enforce_markdown(self, text: str) -> str:
        if self.markdown == "full":
            return text
        text = _apply_outside_code(text, _strip_emphasis)
        if self.markdown == "off":
            text = _ATX_HEADER_RE.sub("", text)
        return text

    def _enforce_links(self, text: str) -> str:
        if self.links != "titles":
            return text
        return _apply_outside_code(text, _title_bare_links)

    def _enforce_emoji(self, text: str) -> str:
        return _strip_emoji(text) if self.emoji == "off" else text

    def _enforce_length(self, text: str) -> str:
        # Deliberately stays a no-op HERE: this method (and apply/verify/enforce)
        # must stay pure/synchronous/deterministic (every other enforcer is a
        # fixed-point string transform re-runnable by verify() with no I/O).
        # Honest compression needs an LLM summary, which is neither — that now
        # happens as a real, additive async step at the ONE production delivery
        # seam (pipeline/steps/deliver.py's _summarize_if_terse), layered on top
        # of this sync pass rather than folded into it.
        if self.length == "terse":
            log.gateway.debug(
                "[format] OutputStyle: length=terse not yet enforced (no-op)",
                extra={"_fields": {"text_len": len(text)}},
            )
        return text

    def apply(self, text: str) -> str:
        """Run every enabled transform in dependency order.

        Tables first (their fenced output is then protected from emphasis/link
        transforms), then markdown, links, emoji, length.
        """
        text = self._enforce_tables(text)
        text = self._enforce_markdown(text)
        text = self._enforce_links(text)
        text = self._enforce_emoji(text)
        text = self._enforce_length(text)
        return text

    def verify(self, text: str) -> str:
        """Post-transform VERIFIER — assert on the produced bytes, repair drift.

        Re-runs each enforcer; because every enforcer is idempotent, a
        well-applied text is a fixed point and this is a silent no-op. Any change
        means the spec did NOT hold (a transform bug, or text that never went
        through :meth:`apply`) — it is logged loudly and the repaired bytes are
        returned. This is the "measured, not asserted" guarantee.
        """
        repaired = text
        for field, enforce in (
            ("tables", self._enforce_tables),
            ("markdown", self._enforce_markdown),
            ("links", self._enforce_links),
            ("emoji", self._enforce_emoji),
        ):
            fixed = enforce(repaired)
            if fixed != repaired:
                log.gateway.warning(
                    "[format] OutputStyle.verify: spec drift repaired",
                    extra={"_fields": {"field": field,
                                       "value": getattr(self, field),
                                       "before_len": len(repaired),
                                       "after_len": len(fixed)}},
                )
                repaired = fixed
        return repaired

    def enforce(self, text: str) -> str:
        """Apply every transform then verify — the single delivery-seam entry.

        An all-default style is a byte-identical fast path (the back-compat
        baseline: no preference set → output untouched, no logs).
        """
        if self == OutputStyle():
            return text
        log.gateway.debug(
            "[format] OutputStyle.enforce: entry",
            extra={"_fields": {"style": self.model_dump(), "text_len": len(text)}},
        )
        result = self.verify(self.apply(text))
        log.gateway.info(
            "[format] OutputStyle.enforce: exit",
            extra={"_fields": {"changed": result != text,
                               "before_len": len(text), "after_len": len(result)}},
        )
        return result

    def describe_rules(self) -> list[str]:
        """The enforced style as plain, observable rules (not field names).

        Shared wording for the LS4 feedback confirmation and the LS5 ``/style``
        command, so the plain-language description can never drift from (or
        between) the surfaces that read the active style back to the user.
        """
        rules: list[str] = []
        if self.markdown in ("minimal", "off"):
            rules.append("no asterisks")
        if self.tables == "off":
            rules.append("no raw tables")
        if self.links == "titles":
            rules.append("links shown as titles")
        if self.emoji == "off":
            rules.append("no emoji")
        if self.length == "terse":
            rules.append("replies kept short")
        return rules


# Field names of the style record — derived from the model (no hardcoded list to
# drift) so callers can tell a style sub-field from another preference key.
OUTPUT_STYLE_FIELDS: frozenset[str] = frozenset(OutputStyle.model_fields)


# --------------------------------------------------------------------------- #
# LS2 mechanical transforms (channel-agnostic GFM in, GFM out)                #
# --------------------------------------------------------------------------- #
# These run at the channel-AGNOSTIC delivery seam and emit GFM, which each
# adapter's existing converter then renders in ITS native parse mode (Telegram
# ``to_telegram_markdownv2`` already escapes link labels/URLs; Slack mrkdwn; CLI
# plain). So link-titling emits GFM ``[Title](URL)`` rather than channel-specific
# HTML — see :func:`_title_bare_links`.

# Emphasis pairs, double-delimiter before single so ``**`` is not read as two
# italics; mirrors the formatter's GFM regexes but STRIPS instead of converting.
_EMPH_BOLD_STAR_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL | re.UNICODE)
_EMPH_BOLD_UNDER_RE = re.compile(r"__(.+?)__", re.DOTALL | re.UNICODE)
_EMPH_STRIKE_RE = re.compile(r"~~(.+?)~~", re.DOTALL | re.UNICODE)
_EMPH_ITALIC_STAR_RE = re.compile(r"\*(.+?)\*", re.UNICODE)
_EMPH_ITALIC_UNDER_RE = re.compile(r"_(.+?)_", re.UNICODE)
_ATX_HEADER_RE = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+")

# Code spans/fences are protected from emphasis/link transforms so e.g. ``2 ** 8``
# or a literal URL inside a code block is never rewritten.
_CODE_SPAN_RE = re.compile(r"```.*?```|`[^`]*`", re.DOTALL | re.UNICODE)

# A bare URL is an http(s) run NOT already inside a markdown/autolink target —
# the lookbehind skips ``](url)``, ``(url)``, ``<url>`` and ``[url]`` so titling
# is idempotent and never double-wraps an existing link.
_BARE_URL_RE = re.compile(r"""(?<![(\[\]<"'])(https?://[^\s)>\]]+)""", re.UNICODE)
_URL_TRAILING = ".,;:!?)”’\"'"

# Emoji/pictograph codepoint ranges (no English/word list — pure Unicode blocks):
# symbols & pictographs (incl. 🔗 U+1F517), emoticons, transport, supplemental &
# extended-A, regional-indicator flags, dingbats, misc symbols/arrows, plus the
# variation-selector and zero-width-joiner that glue emoji sequences.
_EMOJI_RE = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U00002b00-\U00002bff"
    "\U00002190-\U000021ff\U0001f1e6-\U0001f1ff\U0000fe0f\U0000200d]+",
    re.UNICODE,
)


def _apply_outside_code(text: str, fn: Callable[[str], str]) -> str:
    """Apply ``fn`` to every span OUTSIDE code fences/inline code, verbatim within."""
    if "`" not in text:
        return fn(text)
    out: list[str] = []
    last = 0
    for m in _CODE_SPAN_RE.finditer(text):
        out.append(fn(text[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(fn(text[last:]))
    return "".join(out)


def _strip_emphasis(text: str) -> str:
    """Remove ``*``/``**``/``_``/``__``/``~~`` emphasis delimiters, keep inner text.

    Idempotent: a second pass finds no remaining paired delimiters. ``***x***``
    collapses bold→italic to ``x`` in one pass (bold runs before italic)."""
    text = _EMPH_BOLD_STAR_RE.sub(r"\1", text)
    text = _EMPH_BOLD_UNDER_RE.sub(r"\1", text)
    text = _EMPH_STRIKE_RE.sub(r"\1", text)
    text = _EMPH_ITALIC_STAR_RE.sub(r"\1", text)
    text = _EMPH_ITALIC_UNDER_RE.sub(r"\1", text)
    return text


def _url_title(url: str) -> str:
    """Derive a tappable link title from a URL — its host, ``www.`` stripped."""
    host = urlparse(url).netloc or url
    return host[4:] if host.startswith("www.") else host


def _title_bare_links(text: str) -> str:
    """Wrap each bare URL as a titled GFM link ``[host](url)`` (never a dead 🔗).

    Already-formatted ``[label](url)`` links are left untouched (the lookbehind
    skips them), so this is idempotent. Trailing sentence punctuation stays
    OUTSIDE the link."""
    def repl(m: re.Match[str]) -> str:
        url = m.group(1)
        trail = ""
        while url and url[-1] in _URL_TRAILING:
            trail = url[-1] + trail
            url = url[:-1]
        if not url:
            return m.group(0)
        return f"[{_url_title(url)}]({url}){trail}"

    return _BARE_URL_RE.sub(repl, text)


def _strip_emoji(text: str) -> str:
    """Remove emoji/pictographs and tidy the trailing whitespace they leave."""
    stripped = _EMOJI_RE.sub("", text)
    return re.sub(r"(?m)[ \t]+$", "", stripped)


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


# Telegram wraps a monospace line well before this on a standard phone in
# portrait mode. A row that wraps mid-line visually shreds the column
# alignment _render_block worked to produce — worse than not aligning at all
# (live incident 2026-07-23: a 4-column job-listing table wrapped and looked
# broken on mobile). _render_auto falls back to the plain per-row list instead
# of ever emitting a block wider than this.
_MAX_MOBILE_TABLE_WIDTH = 36

def _block_width(header: list[str], body: list[list[str]]) -> int:
    """The rendered line width _render_block would produce for this table."""
    rows = [header, *body]
    width = max(len(r) for r in rows)
    norm = [r + [""] * (width - len(r)) for r in rows]
    cols = [max(len(norm[r][c]) for r in range(len(norm))) for c in range(width)]
    return sum(cols) + 2 * max(width - 1, 0)


def _render_auto(header: list[str], body: list[list[str]]) -> str:
    """Fenced aligned block when it fits a mobile screen, else the plain list.

    Emphasis markup (e.g. "**Capital One**") is stripped via the shared
    _strip_emphasis for cells bound for the fenced block — inside a ``` fence
    Telegram never parses markdown, so the delimiters would just be literal
    noise there. Left untouched for the plain-list fallback, whose unfenced
    output renders that same markdown correctly through the channel's own
    conversion.
    """
    stripped_header = [_strip_emphasis(c) for c in header]
    stripped_body = [[_strip_emphasis(c) for c in row] for row in body]
    if _block_width(stripped_header, stripped_body) <= _MAX_MOBILE_TABLE_WIDTH:
        return _render_block(stripped_header, stripped_body)
    return _render_plain_list(header, body)


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

"""Shared, channel-agnostic markdown normalisation.

GFM pipe-tables are not representable in Telegram MarkdownV2 or Slack mrkdwn;
left alone their ``|``/``-`` chars render as broken escaped text. We flatten a
detected table into a fenced, column-aligned block, which BOTH channel
converters pass through verbatim via their code-fence stash phase. Detection
anchors on a header row immediately followed by a delimiter row (cells of only
``-``/``:``/spaces/pipes) — a lone ``-`` or a horizontal rule is never a table.
"""

from __future__ import annotations

import re

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
            out.append(_render_block(header, body))
            i = j
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


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

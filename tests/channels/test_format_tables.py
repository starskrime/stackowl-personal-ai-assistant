"""Unit tests for the shared GFM-table flattener (channels._format).

Detection must anchor on the HEADER+DELIMITER pair — never treat a lone dash,
list item, or horizontal rule as a table.
"""

from __future__ import annotations

from stackowl.channels._format import flatten_gfm_tables


def test_pipe_table_flattened_to_fence_with_cells() -> None:
    src = "| Name | Age |\n| --- | --- |\n| Ann | 30 |\n| Bob | 25 |"
    out = flatten_gfm_tables(src)
    assert "```" in out                      # rendered as a verbatim fenced block
    for cell in ("Name", "Age", "Ann", "30", "Bob", "25"):
        assert cell in out
    # the raw GFM delimiter row must not survive as a stray pipe line
    assert "| --- |" not in out


def test_lone_dash_and_hr_untouched() -> None:
    assert flatten_gfm_tables("- a list item") == "- a list item"
    assert flatten_gfm_tables("text\n\n---\n\nmore") == "text\n\n---\n\nmore"


def test_non_table_text_unchanged() -> None:
    assert flatten_gfm_tables("just a sentence") == "just a sentence"


# ---------------------------------------------------------------------------
# C1 — _is_table_row false-positive: pipe in prose must not be detected as row
# ---------------------------------------------------------------------------

def test_pipe_in_prose_not_treated_as_table_row() -> None:
    """A shell command containing | followed by a delimiter-like row must NOT
    be fused into a table block.  The prose line must survive unchanged."""
    src = 'Run: git log | grep "error"\n| --- | --- |\n| body |'
    out = flatten_gfm_tables(src)
    # The prose line must be byte-for-byte unchanged
    assert 'Run: git log | grep "error"' in out
    # The whole thing must NOT be wrapped in a fenced block
    assert "```" not in out


# ---------------------------------------------------------------------------
# I2 — _render_block IndexError when body row has MORE columns than header
# ---------------------------------------------------------------------------

def test_ragged_body_more_columns_than_header() -> None:
    """A body row with extra columns (more than header) must not raise IndexError
    and must produce a fenced block containing all cells."""
    src = "| A | B |\n| --- | --- |\n| 1 | 2 | 3 |"
    out = flatten_gfm_tables(src)
    assert "```" in out
    for cell in ("A", "B", "1", "2", "3"):
        assert cell in out


# ---------------------------------------------------------------------------
# I1 — table inside a fenced code block must not be re-flattened
# ---------------------------------------------------------------------------

def test_table_inside_fence_passes_through_unchanged() -> None:
    """A GFM table shown as an example inside a ``` fence must be left
    completely untouched — no double-fence, no corruption."""
    inner = "| Col1 | Col2 |\n| --- | --- |\n| a | b |"
    src = f"```\n{inner}\n```"
    out = flatten_gfm_tables(src)
    # The inner table lines must survive verbatim
    assert "| Col1 | Col2 |" in out
    assert "| --- | --- |" in out
    # Must still be exactly one fenced block, not nested/double
    assert out.count("```") == 2

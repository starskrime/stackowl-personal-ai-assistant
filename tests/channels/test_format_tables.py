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

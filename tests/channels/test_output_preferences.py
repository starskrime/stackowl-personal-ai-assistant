"""Stored output preferences are ENFORCED at delivery, not merely recalled.

"Memory retrieval ≠ enforced constraint": a learned preference like table-free
output was injected into the prompt but never enforced, so a weak model kept
emitting tables. These cover the deterministic pre-send enforcement: a canonical
`output_tables=off` preference converts GFM tables to a plain list; with no such
preference the text is returned byte-for-byte (baseline preserved).
"""

from __future__ import annotations

from stackowl.channels._format import (
    apply_output_preferences,
    flatten_gfm_tables,
    tables_to_plain_list,
)

_TABLE = (
    "Here is the data:\n\n"
    "| Name | Age |\n"
    "| --- | --- |\n"
    "| Bob | 3 |\n"
    "| Sue | 4 |\n"
)


def test_tables_to_plain_list_removes_pipes_and_fences() -> None:
    out = tables_to_plain_list(_TABLE)
    assert "|" not in out
    assert "```" not in out
    assert "---" not in out
    # The data survives as readable lines.
    assert "Name: Bob" in out and "Age: 3" in out
    assert "Name: Sue" in out and "Age: 4" in out


def test_apply_output_preferences_noop_when_unset() -> None:
    assert apply_output_preferences(_TABLE, {}) == _TABLE
    assert apply_output_preferences(_TABLE, {"output_tables": "on"}) == _TABLE
    assert apply_output_preferences("plain text", {"output_tables": "off"}) == "plain text"


def test_apply_output_preferences_enforces_no_tables() -> None:
    for value in ("off", "OFF", "false", "no", "0"):
        out = apply_output_preferences(_TABLE, {"output_tables": value})
        assert "|" not in out, f"value={value!r} did not disable tables"
        assert "Name: Bob" in out


def test_enforcement_is_terminal_not_just_flatten() -> None:
    """Distinct from flatten (which keeps a fenced table): enforcement removes the
    table form entirely."""
    flattened = flatten_gfm_tables(_TABLE)
    assert "```" in flattened  # flatten keeps a fenced block
    enforced = apply_output_preferences(_TABLE, {"output_tables": "off"})
    assert "```" not in enforced  # enforcement does not

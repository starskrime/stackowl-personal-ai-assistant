"""Stored output preferences are ENFORCED at delivery, not merely recalled.

"Memory retrieval ≠ enforced constraint": a learned preference like table-free
output was injected into the prompt but never enforced, so a weak model kept
emitting tables. These cover the deterministic pre-send enforcement: a canonical
`output_tables=off` preference converts GFM tables to a plain list; with no such
preference the text is returned byte-for-byte (baseline preserved).
"""

from __future__ import annotations

import json

from stackowl.channels._format import (
    OUTPUT_STYLE_KEY,
    OutputStyle,
    apply_output_preferences,
    flatten_gfm_tables,
    load_output_style,
    resolve_output_style,
    tables_to_plain_list,
)
from stackowl.db.pool import DbPool
from stackowl.memory.preferences import GLOBAL_OWNER_KEY, PreferenceStore

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


# --------------------------------------------------------------------------- #
# Structured output_style record (LS1) — storage vocabulary + read helper      #
# --------------------------------------------------------------------------- #
def test_output_style_partial_is_valid_with_defaults() -> None:
    style = OutputStyle(markdown="minimal")
    assert style.markdown == "minimal"
    # Unset fields fall back to no-op defaults.
    assert style.links == "inline"
    assert style.tables == "on"
    assert style.emoji == "on"
    assert style.length == "normal"


def test_resolve_reads_stored_json_record() -> None:
    prefs = {OUTPUT_STYLE_KEY: json.dumps({"markdown": "minimal", "links": "titles"})}
    style = resolve_output_style(prefs)
    assert style.markdown == "minimal"
    assert style.links == "titles"
    assert style.tables == "on"  # unset → default


def test_resolve_output_tables_alias_reads_through_as_tables() -> None:
    # Back-compat: a bare output_tables=off resolves to tables=off.
    assert resolve_output_style({"output_tables": "off"}).tables == "off"
    assert resolve_output_style({"output_tables": "on"}).tables == "on"
    # Explicit style.tables wins over the legacy alias.
    prefs = {OUTPUT_STYLE_KEY: json.dumps({"tables": "on"}), "output_tables": "off"}
    assert resolve_output_style(prefs).tables == "on"


def test_resolve_corrupt_record_degrades_to_defaults() -> None:
    style = resolve_output_style({OUTPUT_STYLE_KEY: "not json{{"})
    assert style == OutputStyle()  # all defaults, no raise


async def test_load_output_style_channel_overrides_global(tmp_db: DbPool) -> None:
    store = PreferenceStore(db=tmp_db)
    # Global says tables off + markdown full; channel overrides markdown to minimal.
    await store.set(GLOBAL_OWNER_KEY, OUTPUT_STYLE_KEY, json.dumps({"markdown": "full"}))
    await store.set(GLOBAL_OWNER_KEY, "output_tables", "off")
    await store.set("telegram:42", OUTPUT_STYLE_KEY, json.dumps({"markdown": "minimal"}))
    style = await load_output_style(store, "telegram:42")
    assert style.markdown == "minimal"  # channel wins
    assert style.tables == "off"  # global alias still applies


# --------------------------------------------------------------------------- #
# LS2 deterministic enforcement + verifier — assert on POST-SEAM bytes         #
# --------------------------------------------------------------------------- #
def _prefs(**fields: str) -> dict[str, str]:
    return {OUTPUT_STYLE_KEY: json.dumps(fields)}


def test_markdown_minimal_strips_bold_artifacts() -> None:
    src = "Here is **bold** and *italic* and __also__ and ~~gone~~ text."
    out = apply_output_preferences(src, _prefs(markdown="minimal"))
    assert "*" not in out and "~" not in out and "__" not in out
    # Inner text survives — only the delimiters are removed.
    for word in ("bold", "italic", "also", "gone", "text"):
        assert word in out


def test_markdown_minimal_leaves_code_asterisks_untouched() -> None:
    src = "math `2 ** 8` and ```\nx = a ** b ** c\n``` stay."
    out = apply_output_preferences(src, _prefs(markdown="minimal"))
    assert "2 ** 8" in out  # inline code preserved verbatim
    assert "a ** b ** c" in out  # fenced code preserved verbatim


def test_markdown_off_also_strips_headers() -> None:
    out = apply_output_preferences("# Title\n\nBody **x**", _prefs(markdown="off"))
    assert "#" not in out and "*" not in out
    assert "Title" in out and "Body" in out


def test_links_titles_wraps_bare_url_no_dead_link() -> None:
    src = "See https://www.example.com/ai-news for the story."
    out = apply_output_preferences(src, _prefs(links="titles"))
    assert "[example.com](https://www.example.com/ai-news)" in out
    assert "🔗" not in out
    # The raw URL no longer appears outside the link target.
    assert "See https://" not in out


def test_links_titles_preserves_existing_markdown_link() -> None:
    src = "Read [the report](https://example.com/r) now."
    out = apply_output_preferences(src, _prefs(links="titles"))
    assert out == src  # already titled → untouched


def test_links_titles_keeps_trailing_punctuation_outside_link() -> None:
    out = apply_output_preferences("Go to https://example.com.", _prefs(links="titles"))
    assert "[example.com](https://example.com)." in out


def test_emoji_off_strips_emoji_keeps_text() -> None:
    out = apply_output_preferences("Done ✅ shipped 🚀 link 🔗 here", _prefs(emoji="off"))
    assert "✅" not in out and "🚀" not in out and "🔗" not in out
    assert "Done" in out and "shipped" in out and "here" in out


def test_idempotent_apply_twice_equals_once() -> None:
    prefs = _prefs(markdown="minimal", links="titles", emoji="off", tables="off")
    src = (
        "## News ✅\n\n**Big** story at https://www.site.com/x 🚀\n\n"
        "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
    )
    once = apply_output_preferences(src, prefs)
    twice = apply_output_preferences(once, prefs)
    assert once == twice


def test_no_style_set_is_byte_identical() -> None:
    src = "Keep **this** exactly 🚀 with https://x.com and a # heading."
    assert apply_output_preferences(src, {}) == src
    # An all-default explicit style is also a no-op.
    assert apply_output_preferences(src, _prefs()) == src


def test_tables_off_via_style_still_flattens() -> None:
    out = apply_output_preferences(_TABLE, _prefs(tables="off"))
    assert "|" not in out and "Name: Bob" in out


def test_verifier_catches_and_repairs_violation() -> None:
    # Feed the verifier text that VIOLATES the spec (raw, un-applied) and prove it
    # repairs to a conforming result — the "measured on produced bytes" guarantee.
    style = OutputStyle(markdown="minimal", links="titles")
    bad = "Stray **bold** and bare https://example.com/x remain."
    repaired = style.verify(bad)
    assert "*" not in repaired
    assert "[example.com](https://example.com/x)" in repaired
    # And the repaired bytes are themselves a fixed point.
    assert style.verify(repaired) == repaired

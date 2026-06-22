"""Live-progress vocabulary: enum-keyed, i18n-safe, fallback-never-leaks."""

from __future__ import annotations

import pytest

from stackowl.pipeline.progress import vocabulary
from stackowl.pipeline.progress.vocabulary import ProgressKey, coerce_key, render


def test_render_basic_phrase_includes_glyph_and_text() -> None:
    out = render(ProgressKey.SEARCH_WEB)
    assert "Searching the web" in out
    assert out.startswith("🔎")


def test_render_count_plural_and_singular() -> None:
    assert "1 file" not in render(ProgressKey.READ_FILES, count=1)  # singular form
    assert "Reading a file" in render(ProgressKey.READ_FILES, count=1)
    assert "Reading 3 files" in render(ProgressKey.READ_FILES, count=3)


def test_render_skill_slot() -> None:
    out = render(ProgressKey.SKILL_RUN, skill="research")
    assert "research" in out


def test_unknown_key_falls_back_to_think_never_tool_name() -> None:
    # An unmapped raw tool identifier must degrade to the generic localized
    # phrase, NEVER echo the tool name back to the user.
    out = render("mcp__plugin_ecc_github__create_branch")
    assert "create_branch" not in out
    assert "Thinking" in out


def test_coerce_key_handles_enum_string_and_garbage() -> None:
    assert coerce_key(ProgressKey.SYNTH) is ProgressKey.SYNTH
    assert coerce_key("SEARCH_WEB") is ProgressKey.SEARCH_WEB
    assert coerce_key("not-a-key") is ProgressKey.THINK
    assert coerce_key(None) is ProgressKey.THINK


def test_non_english_bundle_renders_and_locks_translatability() -> None:
    # A sibling locale bundle must render in that language — proving the logic
    # keys on the enum, not on hardcoded English strings.
    vocabulary.register_bundle(
        "tr",
        {
            ProgressKey.SEARCH_WEB: "İnternette aranıyor…",
            ProgressKey.THINK: "Düşünüyorum…",
        },
    )
    out = render(ProgressKey.SEARCH_WEB, lang="tr")
    assert "aranıyor" in out
    # A key missing from the sibling bundle falls back to that locale's THINK,
    # not English.
    missing = render(ProgressKey.RUN_CMD, lang="tr")
    assert "Düşünüyorum" in missing


def test_auto_and_unknown_lang_fall_back_to_english() -> None:
    assert "Searching the web" in render(ProgressKey.SEARCH_WEB, lang="auto")
    assert "Searching the web" in render(ProgressKey.SEARCH_WEB, lang="zz-unknown")


@pytest.mark.parametrize("key", list(ProgressKey))
def test_every_key_renders_without_raising(key: ProgressKey) -> None:
    out = render(key, count=2, skill="x")
    assert isinstance(out, str) and out

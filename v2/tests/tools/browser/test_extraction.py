"""Tests for browser/_extraction.py — Trafilatura fallback + link parser."""

from __future__ import annotations

from stackowl.tools.browser._extraction import extract_links, extract_markdown


class TestExtractLinks:
    def test_finds_anchor(self) -> None:
        html = '<a href="/foo">Foo</a>'
        links = extract_links(html, base_url="https://example.com/")
        assert links == [{"href": "https://example.com/foo", "text": "Foo"}]

    def test_resolves_relative_with_base(self) -> None:
        html = '<a href="bar.html">Bar</a>'
        links = extract_links(html, base_url="https://example.com/dir/")
        assert links[0]["href"] == "https://example.com/dir/bar.html"

    def test_strips_inner_tags_from_text(self) -> None:
        html = '<a href="/x"><span>Hello</span> <b>world</b></a>'
        links = extract_links(html, base_url="https://x.com/")
        assert "Hello" in links[0]["text"]
        assert "world" in links[0]["text"]
        assert "<" not in links[0]["text"]


class TestExtractMarkdown:
    def test_strips_scripts_and_styles_in_fallback(self) -> None:
        html = "<html><script>alert(1)</script><body>Hello world</body></html>"
        out = extract_markdown(html)
        assert "alert(1)" not in out
        assert "Hello world" in out

    def test_returns_text_even_on_empty_body(self) -> None:
        # No-body input should not raise.
        out = extract_markdown("<html></html>")
        assert isinstance(out, str)

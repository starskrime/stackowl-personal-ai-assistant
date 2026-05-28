"""Content extraction helpers — Trafilatura wrapper + link parsing."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from stackowl.infra.observability import log


def extract_markdown(html: str, *, include_links: bool = True) -> str:
    """Convert HTML to clean markdown via Trafilatura. Returns the HTML body
    text when Trafilatura is unavailable or returns nothing useful."""
    try:
        import trafilatura
    except ImportError:
        log.engine.warning("[browser] extract_markdown: trafilatura missing — returning raw text")
        return _strip_tags(html)
    try:
        out = trafilatura.extract(
            html,
            output_format="markdown",
            include_links=include_links,
            include_images=False,
            include_tables=True,
            no_fallback=False,
        )
    except Exception as exc:
        log.engine.warning("[browser] extract_markdown: trafilatura failed", exc_info=exc)
        return _strip_tags(html)
    if not out:
        return _strip_tags(html)
    return str(out)


def extract_links(html: str, base_url: str) -> list[dict[str, str]]:
    """Return a list of ``{"href", "text"}`` dicts for every anchor in the page."""
    import re

    pattern = re.compile(r'<a\s[^>]*?href=["\']([^"\']+)["\'][^>]*?>(.*?)</a>', re.IGNORECASE | re.DOTALL)
    text_strip = re.compile(r"<[^>]+>")
    out: list[dict[str, str]] = []
    for m in pattern.finditer(html):
        href = m.group(1)
        try:
            href_abs = urljoin(base_url, href)
        except Exception:
            href_abs = href
        text = text_strip.sub("", m.group(2)).strip()
        if text or href_abs:
            out.append({"href": href_abs, "text": text})
    return out


def _strip_tags(html: str) -> str:
    """Minimal HTML strip fallback when Trafilatura is unavailable."""
    import re
    no_script = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.IGNORECASE)
    no_style = re.sub(r"<style[\s\S]*?</style>", "", no_script, flags=re.IGNORECASE)
    no_tags = re.sub(r"<[^>]+>", " ", no_style)
    return re.sub(r"\s+", " ", no_tags).strip()


async def index_dom_elements(page: Any) -> list[dict[str, Any]]:
    """Annotate every interactive element with a numeric index and return the list.

    Used by the browser_browse meta-tool's DOM-perception path. Each element gets:
    ``{index, tag, role, text, selector}``.
    """
    js = """
    () => {
      const SELECTORS = 'a, button, input, textarea, select, [role="button"], [role="link"], [role="textbox"]';
      const nodes = Array.from(document.querySelectorAll(SELECTORS));
      const visible = nodes.filter(n => {
        const r = n.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return false;
        const s = window.getComputedStyle(n);
        return s.visibility !== 'hidden' && s.display !== 'none';
      });
      return visible.slice(0, 200).map((n, i) => ({
        index: i,
        tag: n.tagName.toLowerCase(),
        role: n.getAttribute('role') || '',
        text: (n.innerText || n.value || n.placeholder || '').trim().slice(0, 80),
        href: n.getAttribute('href') || '',
        id: n.id || '',
        name: n.getAttribute('name') || ''
      }));
    }
    """
    try:
        result = await page.evaluate(js)
        return list(result) if isinstance(result, list) else []
    except Exception as exc:
        log.engine.warning("[browser] index_dom_elements: failed", exc_info=exc)
        return []

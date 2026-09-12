"""It is an APP on a phone, and that is four declarations rather than an opinion.

**The operator's verdict, 2026-09-12:** *"Dashboard not an enterprice level and
modern on hitech view ... make it mobile optimized looks like app bit an
website."* A05.10's gap was MEASURED rather than restated, and the measurement is
what this file pins: the page carried `viewport` and `prefers-color-scheme`, so
the gap was never "nobody thought about mobile" — it was **no manifest, no
`theme-color`, no web-app-capable meta and no app icon**. Those four absences ARE
the difference between a page and an app: they are why it could not be installed,
had no home-screen icon, and kept browser chrome.

THE MANIFEST AND ICON ARE ROUTES, NOT `data:` URIs, and that is forced rather than
chosen: a browser refuses a `data:` manifest. Serving them from this door keeps
the "no external request of any kind" constraint — nothing leaves the origin — and
neither is an `/api/` path, so the route/page bijection is untouched.

AND NEITHER HANDLER MAY READ INSTANCE STATE. A browser fetches a manifest before
anyone has signed in, so it cannot present a credential; the structural exemption
in `test_every_route_handler_calls_authenticate` allows that only for a handler
that cannot reach platform state. `start_url` is therefore RELATIVE — putting the
configured port in it would need `self._settings` and forfeit the exemption.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
from typing import Any

import pytest

from stackowl.control_plane.page import ICON_SVG, INDEX_HTML, MANIFEST_JSON


class _Req:
    headers: dict[str, str] = {}


def _server() -> Any:
    from stackowl.control_plane.server import ControlPlaneServer

    class _Cfg:
        bind_address = "0.0.0.0"  # noqa: S104
        port = 8787

    class _Settings:
        control_plane = _Cfg()

    return ControlPlaneServer(_Settings())  # type: ignore[arg-type]


class TestItCanBeInstalled:
    @pytest.mark.tripwire
    async def test_the_manifest_is_served_and_declares_a_standalone_app(self) -> None:
        res = await _server()._handle_manifest(_Req())  # noqa: SLF001

        assert res.content_type == "application/manifest+json"
        data = json.loads(res.text)
        assert data["display"] == "standalone", "without this it opens in a browser tab"
        assert data["icons"], "an installed app with no icon gets a screenshot"

    @pytest.mark.tripwire
    def test_start_url_is_RELATIVE_so_the_handler_needs_no_settings(self) -> None:
        """The bind is `0.0.0.0` by default and the operator may reach this on any
        address; an absolute `start_url` would be wrong for all but one of them AND
        would make the handler read `self._settings`, which forfeits the unguarded
        exemption a pre-sign-in fetch depends on."""
        data = json.loads(MANIFEST_JSON)

        assert data["start_url"] == "/"
        assert "://" not in json.dumps(data), f"an absolute URL reached the manifest: {data}"

    @pytest.mark.tripwire
    async def test_the_icon_is_served_as_svg(self) -> None:
        res = await _server()._handle_icon(_Req())  # noqa: SLF001

        assert res.content_type == "image/svg+xml"
        assert res.text.lstrip().startswith("<svg")

    @pytest.mark.tripwire
    def test_NEITHER_new_handler_reads_instance_state(self) -> None:
        """The structural carve-out, asserted here as well as in the auth guard —
        because this is the property that makes serving them unauthenticated safe,
        and it is one `self._settings` away from being false."""
        from stackowl.control_plane import server as mod

        src = inspect.getsource(mod.ControlPlaneServer)
        defs = {
            n.name: n
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
        }
        for name in ("_handle_manifest", "_handle_icon"):
            fn = defs[name]
            # THE DOCSTRING IS STRIPPED, and that is not tidiness: the first run
            # of this guard FAILED on correct code because the docstring it reads
            # explains that putting the port here "would need `self._settings`".
            # Fourth time in one session that a guard matched my PROSE rather than
            # my code. A docstring is documentation, not behaviour.
            body = ast.unparse(
                ast.Module(body=[b for b in fn.body if not (
                    isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant)
                    and isinstance(b.value.value, str))], type_ignores=[])
            )
            assert "self._" not in body, (
                f"{name} reads instance state, so it may no longer be served "
                f"without a credential"
            )


class TestTheHeadDeclaresAnApp:
    @pytest.mark.tripwire
    @pytest.mark.parametrize(
        "needle",
        [
            'name="viewport"',
            "viewport-fit=cover",          # or env(safe-area-inset-*) resolves to 0
            'rel="manifest"',
            'rel="icon"',
            "apple-touch-icon",
            "apple-mobile-web-app-capable",
            'name="theme-color"',
        ],
    )
    def test_the_declaration_is_present(self, needle: str) -> None:
        assert needle in INDEX_HTML, f"{needle} is missing — measured absent 2026-09-12"

    @pytest.mark.tripwire
    def test_theme_color_is_declared_for_BOTH_schemes(self) -> None:
        """One `theme-color` paints the phone's chrome wrong in the other theme,
        which is more of the 'this is a web page' feeling than any styling."""
        colours = re.findall(r'name="theme-color"[^>]*', INDEX_HTML)

        assert len(colours) == 2, f"expected one per scheme, found {colours}"
        assert any("prefers-color-scheme: light" in c for c in colours)
        assert any("prefers-color-scheme: dark" in c for c in colours)


class TestTheDesignSystemIsAsystem:
    @pytest.mark.tripwire
    def test_the_dark_palette_is_defined_in_ALL_THREE_viewer_states(self) -> None:
        """A viewer has three: an explicit choice stamps `data-theme`, and the
        DEFAULT setting stamps nothing at all. A palette defined only under
        `[data-theme]` never applies to the common case."""
        assert ":root {" in INDEX_HTML, "no unconditional light palette"
        assert "prefers-color-scheme: dark" in INDEX_HTML
        assert ':root[data-theme="dark"]' in INDEX_HTML, (
            "an explicit dark choice would not beat a light OS"
        )

    @pytest.mark.tripwire
    def test_reduced_motion_is_respected(self) -> None:
        """Measured absent on 2026-09-12, which meant any animation added would be
        unconditional. The seam is the first animation this page has ever had."""
        assert "prefers-reduced-motion" in INDEX_HTML

    @pytest.mark.tripwire
    def test_the_input_font_never_drops_below_the_iOS_ZOOM_FLOOR(self) -> None:
        """Below 16px, iOS Safari zooms on focus — the single loudest signal that
        a surface is a web page rather than an app. A floor, not a preference."""
        block = re.search(r"\n  input \{(.*?)\}", INDEX_HTML, re.S)
        assert block, "the input rule moved; this guard has gone blind"
        size = re.search(r"font:[^;]*?(\d+(?:\.\d+)?)px", block.group(1))
        assert size and float(size.group(1)) >= 16, (
            f"input font is {size.group(1) if size else '?'}px — iOS will zoom"
        )

    @pytest.mark.tripwire
    def test_the_mark_is_inline_and_takes_the_current_colour(self) -> None:
        """No image host, no data URI in the page, and one path that works at 26px
        and at 16px. `currentColor` is what lets it invert with the theme."""
        assert '<svg class="mark"' in INDEX_HTML
        assert 'fill="currentColor"' in INDEX_HTML
        assert "<img" not in INDEX_HTML, "a raster would need a request or a blob"

    @pytest.mark.tripwire
    def test_the_icon_route_and_the_header_mark_draw_the_SAME_path(self) -> None:
        """Two drawings of one logo is the two-copies-of-one-rule shape wearing
        art direction. They are separate constants because one needs its own
        ground for a home-screen tile — so the GEOMETRY is what must match."""
        def first_path(svg: str) -> str:
            """Canonical form: every command letter separated, whitespace
            collapsed. Comparing raw text called two IDENTICAL paths different
            because one wrapped after `6.9` and the other before `L10.6` — SVG
            does not care and neither should this. Stripping whitespace entirely
            would be worse: it would fuse `M3.5 2` into `M3.52` and could call two
            DIFFERENT paths equal, which is the check-passing-for-the-wrong-reason
            trap this repo pays for most."""
            m = re.search(r'd="([^"]+)"', svg)
            assert m, "no path found"
            spaced = re.sub(r"([A-Za-z])", r" \1 ", m.group(1))
            return " ".join(spaced.split())

        assert first_path(ICON_SVG) == first_path(
            re.search(r'<svg class="mark".*?</svg>', INDEX_HTML, re.S).group(0)  # type: ignore[union-attr]
        )

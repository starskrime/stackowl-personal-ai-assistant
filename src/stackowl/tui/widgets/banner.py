"""Banner — pinned StackOwl wordmark + tagline docked at the top of the screen."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape
from textual.containers import Vertical
from textual.widgets import Static

from stackowl.infra.observability import log
from stackowl.tui.i18n import localize

if TYPE_CHECKING:
    from textual.app import ComposeResult

_RULE_TOP_ID = "banner-rule-top"
_RULE_BOTTOM_ID = "banner-rule-bottom"
_RULE_GLYPH = "─"
_FALLBACK_WIDTH = 80


class Banner(Vertical):
    """Pinned wordmark banner docked to the top of the screen.

    Subclasses :class:`~textual.containers.Vertical` (not a bare ``Widget``) so
    its rule / art / tagline child Statics stack vertically — a plain ``Widget``
    overlaps them at the same position. ``dock: top`` (without ``layer``) both
    pins the banner and reserves its rows, so the transcript flows beneath it
    rather than being overlaid.

    The ASCII art is ported verbatim from the legacy ``header.ts`` wordmark.
    The top half of the logo is rendered amber and the bottom half red; those
    colors map to the ``warning`` / ``error`` design tokens, which auto-
    downsample cleanly across the 5 color-capability ``tcss`` variants (and the
    rule maps to ``success``/green).  Per product decision the banner is always
    rendered full width — it clips on terminals narrower than the wordmark,
    which is intentional rather than wrapped or scaled.

    Colors live entirely in CSS tokens (``$color-banner-*``); the Python layer
    never names a literal color.
    """

    # Ported verbatim from src/cli/v2/io/header.ts (LOGO_LINES, lines 8-13).
    # (line, bright) — bright=amber (top half), not-bright=red (bottom half).
    LOGO_LINES: tuple[tuple[str, bool], ...] = (
        ("███████╗████████╗ █████╗  ██████╗██╗  ██╗ ██████╗ ██╗    ██╗██╗     ", True),
        ("██╔════╝╚══██╔══╝██╔══██╗██╔════╝██║ ██╔╝██╔═══██╗██║    ██║██║     ", True),
        ("███████╗   ██║   ███████║██║     █████╔╝ ██║   ██║██║ █╗ ██║██║     ", True),
        ("╚════██║   ██║   ██╔══██║██║     ██╔═██╗ ██║   ██║██║███╗██║██║     ", False),
        ("███████║   ██║   ██║  ██║╚██████╗██║  ██╗╚██████╔╝╚███╔███╔╝███████╗", False),
        ("╚══════╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝ ╚═════╝  ╚══╝╚══╝╚══════╝", False),
    )

    DEFAULT_CSS = """
    Banner {
        dock: top;
        height: 9;
        width: 100%;
        overflow-x: hidden;
        background: $color-bg;
    }
    Banner .banner-rule { color: $color-banner-rule; height: 1; }
    Banner .banner-amber { color: $color-banner-amber; height: 1; text-style: bold; }
    Banner .banner-red { color: $color-banner-red; height: 1; text-style: bold; }
    Banner .banner-tagline { color: $color-text-secondary; height: 1; }
    """

    def compose(self) -> ComposeResult:
        """Yield the rule / art / tagline / rule child Statics (top → bottom)."""
        log.tui.debug(
            "[tui] banner.compose: entry",
            extra={"_fields": {"art_lines": len(self.LOGO_LINES)}},
        )
        yield Static(id=_RULE_TOP_ID, classes="banner-rule")
        for index, (line, bright) in enumerate(self.LOGO_LINES):
            css_class = "banner-amber" if bright else "banner-red"
            yield Static(line, id=f"banner-art-{index}", classes=css_class)
        yield Static(
            self._tagline_text(),
            id="banner-tagline",
            classes="banner-tagline",
            markup=True,
        )
        yield Static(id=_RULE_BOTTOM_ID, classes="banner-rule")
        log.tui.debug(
            "[tui] banner.compose: exit",
            extra={"_fields": {"art_lines": len(self.LOGO_LINES)}},
        )

    def on_mount(self) -> None:
        """Render the horizontal rules to the initial terminal width."""
        log.tui.debug(
            "[tui] banner.on_mount: entry",
            extra={"_fields": {"width": self.size.width}},
        )
        self._render_rules()
        log.tui.debug(
            "[tui] banner.on_mount: exit",
            extra={"_fields": {"width": self.size.width}},
        )

    def on_resize(self) -> None:
        """Recompute the rules whenever the terminal width changes."""
        log.tui.debug(
            "[tui] banner.on_resize: entry",
            extra={"_fields": {"width": self.size.width}},
        )
        self._render_rules()
        log.tui.debug(
            "[tui] banner.on_resize: exit",
            extra={"_fields": {"width": self.size.width}},
        )

    # ------------------------------------------------------------------ render

    def _tagline_text(self) -> str:
        """Localized tagline markup (leading space mirrors the legacy header).

        Localized values are ``escape``-d so a future locale string containing
        Rich markup characters (``[``/``]``) cannot corrupt the rendering or
        raise a ``MarkupError`` — the platform is multilingual, so this is a
        real concern once non-English tables are registered.
        """
        primary = escape(localize("banner.tagline_primary"))
        secondary = escape(localize("banner.tagline_secondary"))
        return f" [b]{primary}[/b][dim] • {secondary}[/dim]"

    def _render_rules(self) -> None:
        """Fill both rule Statics with a full-width run of the rule glyph."""
        width = self.size.width or _FALLBACK_WIDTH
        log.tui.debug(
            "[tui] banner._render_rules: entry",
            extra={"_fields": {"width": width}},
        )
        rule = _RULE_GLYPH * width
        for rule_id in (_RULE_TOP_ID, _RULE_BOTTOM_ID):
            try:
                rule_widget = self.query_one(f"#{rule_id}", Static)
            except Exception as exc:  # widget not mounted yet (tests)
                log.tui.warning(
                    "[tui] banner._render_rules: rule widget unavailable",
                    exc_info=exc,
                    extra={"_fields": {"rule_id": rule_id, "width": width}},
                )
                continue
            rule_widget.update(rule)
        log.tui.debug(
            "[tui] banner._render_rules: exit",
            extra={"_fields": {"width": width}},
        )

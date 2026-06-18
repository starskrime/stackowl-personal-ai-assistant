"""ColorCapabilityDetector — detects terminal color capability tier.

Inspects environment variables to classify the host terminal into one of four
:class:`ColorTier` buckets so the TUI can load a matching stylesheet.  The
``--plain`` CLI flag and narrow-terminal MINIMAL collapse are deferred to a
future story; this module only resolves *colour* capability.

Production call site:
    ``ColorCapabilityDetector().detect(dict(os.environ))``

Injecting ``env`` keeps the detector trivially testable — never read
``os.environ`` inside :meth:`ColorCapabilityDetector.detect`.
"""

from __future__ import annotations

import enum

from stackowl.infra.observability import log


class ColorTier(enum.Enum):
    """Terminal colour-rendering capability buckets."""

    MONOCHROME = "monochrome"   # no ANSI colour at all
    COLOR_16 = "16color"        # 16-colour ANSI
    COLOR_256 = "256color"      # 256-colour palette
    COLOR_24BIT = "24bit"       # full truecolor (24-bit RGB)


_TIER_TO_STYLESHEET: dict[ColorTier, str] = {
    ColorTier.MONOCHROME: "stackowl-monochrome.tcss",
    ColorTier.COLOR_16: "stackowl-16.tcss",
    ColorTier.COLOR_256: "stackowl-256.tcss",
    ColorTier.COLOR_24BIT: "stackowl-24bit.tcss",
}

# Conservative TERM whitelist that we know is 16-colour only.
_TERMS_16COLOR: frozenset[str] = frozenset(
    {"linux", "vt100", "vt102", "ansi", "xterm"}
)

# COLORTERM values that promise truecolor.
_COLORTERM_TRUECOLOR: frozenset[str] = frozenset({"truecolor", "24bit"})


class ColorCapabilityDetector:
    """Resolves a :class:`ColorTier` from an injected environment mapping."""

    def detect(self, env: dict[str, str] | None = None) -> ColorTier:
        """Apply precedence rules in order and return the best-fit tier.

        Precedence (highest-priority first):

        1. ``NO_COLOR`` present (any value) → :attr:`ColorTier.MONOCHROME`
        2. ``CLICOLOR`` == "0" → :attr:`ColorTier.MONOCHROME`
        3. ``TMUX`` set AND ``TERM`` contains "screen" → :attr:`ColorTier.COLOR_256`
        4. ``COLORTERM`` in {"truecolor", "24bit"} → :attr:`ColorTier.COLOR_24BIT`
        5. ``COLORTERM`` set to any other value → :attr:`ColorTier.COLOR_256`
        6. ``TERM`` in known 16-colour list → :attr:`ColorTier.COLOR_16`
        7. Default → :attr:`ColorTier.COLOR_256`

        Args:
            env: Optional environment mapping.  Pass ``dict(os.environ)`` in
                production; pass a controlled dict in tests.  ``None`` is
                treated as an empty mapping.

        Returns:
            The resolved :class:`ColorTier`.
        """
        log.tui.debug(
            "[tui] color_caps.detect: entry",
            extra={"_fields": {"env_keys": sorted((env or {}).keys())}},
        )
        env_map: dict[str, str] = env or {}

        # 1. NO_COLOR — universal opt-out (https://no-color.org).
        if "NO_COLOR" in env_map:
            tier = ColorTier.MONOCHROME
            log.tui.debug(
                "[tui] color_caps.detect: NO_COLOR set",
                extra={"_fields": {"tier": tier.value}},
            )
            return self._log_and_return(tier)

        # 2. CLICOLOR=0 — BSD-style opt-out.
        if env_map.get("CLICOLOR") == "0":
            tier = ColorTier.MONOCHROME
            log.tui.debug(
                "[tui] color_caps.detect: CLICOLOR=0",
                extra={"_fields": {"tier": tier.value}},
            )
            return self._log_and_return(tier)

        term_value: str = env_map.get("TERM", "")
        colorterm_value: str = env_map.get("COLORTERM", "")

        # 3. tmux — usually 256 colour even when TERM=screen-256color.
        if "TMUX" in env_map and "screen" in term_value:
            tier = ColorTier.COLOR_256
            log.tui.debug(
                "[tui] color_caps.detect: tmux session",
                extra={"_fields": {"tier": tier.value, "term": term_value}},
            )
            return self._log_and_return(tier)

        # 4. COLORTERM declares truecolor.
        if colorterm_value in _COLORTERM_TRUECOLOR:
            tier = ColorTier.COLOR_24BIT
            log.tui.debug(
                "[tui] color_caps.detect: COLORTERM truecolor",
                extra={
                    "_fields": {
                        "tier": tier.value,
                        "colorterm": colorterm_value,
                    }
                },
            )
            return self._log_and_return(tier)

        # 5. COLORTERM present but generic — assume 256.
        if colorterm_value:
            tier = ColorTier.COLOR_256
            log.tui.debug(
                "[tui] color_caps.detect: COLORTERM generic",
                extra={
                    "_fields": {
                        "tier": tier.value,
                        "colorterm": colorterm_value,
                    }
                },
            )
            return self._log_and_return(tier)

        # 6. TERM is in a known 16-colour-only family.
        if term_value in _TERMS_16COLOR:
            tier = ColorTier.COLOR_16
            log.tui.debug(
                "[tui] color_caps.detect: TERM 16color",
                extra={"_fields": {"tier": tier.value, "term": term_value}},
            )
            return self._log_and_return(tier)

        # 7. Default — assume 256 colour, the modern baseline.
        tier = ColorTier.COLOR_256
        log.tui.debug(
            "[tui] color_caps.detect: default",
            extra={"_fields": {"tier": tier.value, "term": term_value}},
        )
        return self._log_and_return(tier)

    def _log_and_return(self, tier: ColorTier) -> ColorTier:
        """Emit the public info-level detection record and return ``tier``."""
        log.tui.info(
            "[tui] color_caps: detected",
            extra={"_fields": {"tier": tier.value}},
        )
        return tier

    def stylesheet_name(self, tier: ColorTier) -> str:
        """Return the stylesheet filename associated with ``tier``.

        Args:
            tier: The colour tier whose stylesheet should be loaded.

        Returns:
            A bare filename (no path).  The caller composes the absolute path
            relative to the styles directory.
        """
        log.tui.debug(
            "[tui] color_caps.stylesheet_name: entry",
            extra={"_fields": {"tier": tier.value}},
        )
        name: str = _TIER_TO_STYLESHEET[tier]
        log.tui.debug(
            "[tui] color_caps.stylesheet_name: exit",
            extra={"_fields": {"tier": tier.value, "name": name}},
        )
        return name

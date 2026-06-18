// StackOwl TUI v2 — design token system. Single source of truth for color, spacing, border, layout, glyphs.

export const colors = {
  brand:        "#F5A623",   // amber — owl avatar, prompt caret, parliament owl name ONLY
  brandDim:     "#A07418",   // dimmed amber for secondary brand moments
  user:         "green",     // ❯ You author tag, success ticks
  success:      "green",
  warning:      "yellow",    // esc esc to stop, parliament round labels, skills overlay
  error:        "red",
  heartbeat:    "#A78BFA",   // purple — heartbeat banner AND mcp overlay (both = external/proactive)
  accent:       "cyan",      // overlay titles, cursor block
  dim:          "gray",      // chrome, dividers, captions
  verdict:      "yellow",    // parliament synthesis verdict border + text
} as const;

export const spacing = {
  xs: 0,
  sm: 1,
  md: 2,
  lg: 4,
} as const;

export const borders = {
  surface:     "round",   // Composer, overlays (CommandPalette, Heartbeat, MCP, Skills)
  emphasis:    "double",  // Parliament verdict only
  subdivision: "single",  // Parliament owl columns
} as const;

export const layout = {
  maxContentCols: 100,   // Composer + Frame max width
  gutterX:        2,     // paddingX inside Frame
  topBarLines:    2,     // TopBar height (2 rows + divider)
  keyCol:         16,    // CommandPalette key column width (replaces magic 16)
  dividerWidth:   38,    // CommandPalette divider length (replaces magic 38)
} as const;

export const glyphs = {
  selection:    "❯",      // list selection prefix everywhere (SessionsScreen, OwlsScreen)
  brandCaret:   "❯",      // Composer prompt caret (same glyph, semantic alias)
  cursor:       "▋",      // streaming cursor block
  verdictScale: "⚖",      // parliament header/verdict glyph
  heartbeatBell:"🔔",     // heartbeat banner default emoji
  divider:      "─",      // repeating divider character
  leftRule:     "│",      // ToolCallCard running state left-rule
  leftRuleTerm: "└",      // ToolCallCard terminal state left-rule
} as const;

// Typography shape — Partial<{bold, dimColor, color}> for use with Ink Text props
// Not Ink-imported — these are just plain objects matching Text prop shapes.
export const typography = {
  title:   { bold: true  as const, color: "cyan"  as const },
  body:    {} as Record<string, never>,
  caption: { dimColor: true as const },
  meta:    { dimColor: true as const },
} as const;

// Convenience type re-exports
export type Colors  = typeof colors;
export type Spacing = typeof spacing;
export type Borders = typeof borders;
export type Layout  = typeof layout;
export type Glyphs  = typeof glyphs;

export const tokens = { colors, spacing, borders, layout, glyphs, typography } as const;
export type Tokens = typeof tokens;
export default tokens;

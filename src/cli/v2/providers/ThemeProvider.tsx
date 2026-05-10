/**
 * TUI v2 ThemeProvider — wired to the design token system.
 * `useTheme()` returns the full token object: { colors, spacing, borders, layout, glyphs, typography }
 */

import React, { createContext, useContext } from "react";
import { tokens } from "../theme/tokens.js";

export type Theme = typeof tokens;

export const defaultTheme: Theme = tokens;

const ThemeContext = createContext<Theme>(defaultTheme);

export function ThemeProvider({
  theme = defaultTheme,
  children,
}: {
  theme?: Theme;
  children: React.ReactNode;
}) {
  return <ThemeContext.Provider value={theme}>{children}</ThemeContext.Provider>;
}

export function useTheme(): Theme {
  return useContext(ThemeContext);
}

/**
 * Catppuccin-inspired palette carried forward from v1 shared/palette.ts.
 * Phase 1 will flesh out full theming; this is the stub for Phase 0.
 */

import React, { createContext, useContext } from "react";

export interface Theme {
  owlColor: string;
  userColor: string;
  toolColor: string;
  heartbeatColor: string;
  dimColor: string;
  errorColor: string;
  successColor: string;
  parliamentColors: string[];
}

export const defaultTheme: Theme = {
  owlColor: "cyan",
  userColor: "green",
  toolColor: "yellow",
  heartbeatColor: "magenta",
  dimColor: "gray",
  errorColor: "red",
  successColor: "green",
  parliamentColors: ["cyan", "blue", "magenta", "yellow"],
};

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

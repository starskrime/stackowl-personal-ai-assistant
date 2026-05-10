import { Text } from "ink";
import { useTheme } from "../providers/ThemeProvider.js";
import { useTerminalCols } from "../input/useTerminalCols.js";

export function TopBar() {
  const { glyphs } = useTheme();
  const cols = useTerminalCols();
  const divider = glyphs.divider.repeat(cols);

  return <Text dimColor>{divider}</Text>;
}

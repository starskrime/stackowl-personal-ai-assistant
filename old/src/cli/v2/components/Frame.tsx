import { Box } from "ink";
import { useTheme } from "../providers/ThemeProvider.js";
import { useTerminalCols } from "../input/useTerminalCols.js";

interface FrameProps {
  children: React.ReactNode;
}

export function Frame({ children }: FrameProps) {
  const { layout } = useTheme();
  const cols = useTerminalCols();
  const width = Math.min(cols, layout.maxContentCols);
  return (
    <Box flexDirection="column" width={width} paddingX={layout.gutterX}>
      {children}
    </Box>
  );
}

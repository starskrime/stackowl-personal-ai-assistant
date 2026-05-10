import { Box, Text } from "ink";
import { useTheme } from "../providers/ThemeProvider.js";
import { useTerminalCols } from "../input/useTerminalCols.js";

// "StackOwl" in slant figlet font
const LOGO: Array<{ line: string; bright: boolean }> = [
  { line: "  / ___/   / /_   ____ _   _____   / /__   / __ \\   _      __   / /  ", bright: true  },
  { line: "  \\__ \\   / __/  / __ `/  / ___/  / //_/  / / / /  | | /| / /  / /  ", bright: true  },
  { line: " ___/ /  / /_   / /_/ /  / /__   / ,<    / /_/ /   | |/ |/ /  / /   ", bright: false },
  { line: "/____/   \\__/   \\__,_/   \\___/  /_/|_|   \\____/    |__/|__/  /_/    ", bright: false },
];

export function EmptyState() {
  const { colors } = useTheme();
  const cols = useTerminalCols();
  const divider = "─".repeat(Math.max(cols, 70));

  return (
    <Box flexDirection="column" paddingY={1}>
      {/* ASCII art logo — top rows bright amber, bottom rows dim for 3D depth */}
      {LOGO.map(({ line, bright }, i) => (
        <Text key={i} bold={bright} color={bright ? colors.brand : colors.brandDim}>
          {line}
        </Text>
      ))}

      {/* Full-width divider */}
      <Text dimColor>{divider}</Text>

      {/* Subtitle */}
      <Box paddingLeft={1}>
        <Text color={colors.brand}>🦉 </Text>
        <Text bold>Personal AI Assistant</Text>
        <Text dimColor> • Challenge Everything</Text>
      </Box>
    </Box>
  );
}

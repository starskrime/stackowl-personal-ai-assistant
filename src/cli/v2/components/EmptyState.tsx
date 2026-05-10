import { Box, Text } from "ink";
import { useTheme } from "../providers/ThemeProvider.js";

// "STACKOWL" in ANSI Shadow figlet font — block chars for 3D depth
const LOGO: Array<{ line: string; bright: boolean }> = [
  { line: "███████╗████████╗ █████╗  ██████╗██╗  ██╗ ██████╗ ██╗    ██╗██╗     ", bright: true  },
  { line: "██╔════╝╚══██╔══╝██╔══██╗██╔════╝██║ ██╔╝██╔═══██╗██║    ██║██║     ", bright: true  },
  { line: "███████╗   ██║   ███████║██║     █████╔╝ ██║   ██║██║ █╗ ██║██║     ", bright: true  },
  { line: "╚════██║   ██║   ██╔══██║██║     ██╔═██╗ ██║   ██║██║███╗██║██║     ", bright: false },
  { line: "███████║   ██║   ██║  ██║╚██████╗██║  ██╗╚██████╔╝╚███╔███╔╝███████╗", bright: false },
  { line: "╚══════╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝ ╚═════╝  ╚══╝╚══╝╚══════╝", bright: false },
];

export function EmptyState() {
  const { colors } = useTheme();

  return (
    <Box flexDirection="column" paddingBottom={1}>
      {LOGO.map(({ line, bright }, i) => (
        <Text key={i} bold={bright} color={bright ? colors.brand : colors.brandDim}>
          {line}
        </Text>
      ))}

      <Box paddingLeft={1} marginTop={1}>
        <Text bold>Personal AI Assistant</Text>
        <Text dimColor> • Challenge Everything</Text>
      </Box>
    </Box>
  );
}

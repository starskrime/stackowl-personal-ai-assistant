import { Box, Text } from "ink";
import { useTheme } from "../providers/ThemeProvider.js";

// Plain ASCII owl — no chalk, safe for Ink Text rendering
const OWL_LINES = [
  "  ,___,   ",
  " ( o.o )  ",
  "  )-W-(   ",
  " /|   |\\  ",
  "/_|___|_\\ ",
];

export function EmptyState() {
  const { colors } = useTheme();
  return (
    <Box flexDirection="column" paddingY={1} paddingLeft={2}>
      {/* Owl + tagline side by side */}
      <Box>
        <Box flexDirection="column" marginRight={2}>
          {OWL_LINES.map((line, i) => (
            <Text key={i} color={colors.brand}>{line}</Text>
          ))}
        </Box>
        <Box flexDirection="column" justifyContent="center">
          <Text bold color={colors.brand}>Your flock of specialist owls</Text>
        </Box>
      </Box>

      {/* Example prompts */}
      <Box flexDirection="column" marginTop={1} paddingLeft={1}>
        <Text dimColor>Try asking:</Text>
        <Text dimColor>{"  \"what's on my plate today?\""}</Text>
        <Text dimColor>{"  \"draft a reply to the last email\""}</Text>
        <Text dimColor>{"  \"convene parliament: should I refactor auth?\""}</Text>
      </Box>

      {/* Hint line */}
      <Box marginTop={1} paddingLeft={1}>
        <Text dimColor>/help · /owls · /skills</Text>
      </Box>
    </Box>
  );
}

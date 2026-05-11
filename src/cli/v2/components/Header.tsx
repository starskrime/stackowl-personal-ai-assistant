/**
 * Header — adaptive Ink component replacing the pre-painted writeHeader().
 * Green rules track terminal width via useTerminalCols() so they reflow on resize.
 * Logo lines are fixed-width box-drawing art; rules around them adapt.
 */

import { Box, Text } from "ink";
import { LOGO_LINES } from "../io/header.js";
import { useTerminalCols } from "../input/useTerminalCols.js";

export function Header() {
  const cols = useTerminalCols();
  const rule = "─".repeat(cols);

  return (
    <Box flexDirection="column" width={cols}>
      <Text color="green">{rule}</Text>
      {LOGO_LINES.map(({ text, bright }, i) => (
        <Text key={i} bold color={bright ? "yellow" : "red"}>{text}</Text>
      ))}
      <Text> <Text bold>Personal AI Assistant</Text><Text dimColor> • Challenge Everything</Text></Text>
      <Text color="green">{rule}</Text>
    </Box>
  );
}

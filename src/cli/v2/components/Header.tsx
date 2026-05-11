/**
 * Header — adaptive Ink component replacing the pre-painted writeHeader().
 * Rules use Box borders (Yoga layout dimensions) so they track real terminal width
 * without depending on React state, which can be stale during Ink's own resize renders.
 */

import { memo } from "react";
import { Box, Text } from "ink";
import { LOGO_LINES } from "../io/header.js";

function HeaderImpl() {
  return (
    <Box flexDirection="column">
      <Box width="100%" borderStyle="single" borderTop borderBottom={false} borderLeft={false} borderRight={false} borderColor="green" />
      {LOGO_LINES.map(({ text, bright }, i) => (
        <Text key={i} bold color={bright ? "yellow" : "red"}>{text}</Text>
      ))}
      <Text> <Text bold>Personal AI Assistant</Text><Text dimColor> • Challenge Everything</Text></Text>
      <Box width="100%" borderStyle="single" borderTop borderBottom={false} borderLeft={false} borderRight={false} borderColor="green" />
    </Box>
  );
}

export const Header = memo(HeaderImpl);

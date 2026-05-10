/**
 * ParliamentScreen — alt-screen modal during multi-owl debates.
 *
 * Phase 0 stub. Wired in Phase 2 (parliament.round.started → mode:"parliament").
 *
 * Layout: each owl in a column, streaming positions in parallel, gavel on synthesis.
 */


import { Box, Text } from "ink";

export function ParliamentScreen() {
  return (
    <Box flexDirection="column">
      <Text bold>⚖️  Parliament — Phase 2</Text>
    </Box>
  );
}

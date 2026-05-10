/** Multi-line input, history, paste. Phase 1. */

import { Box, Text } from "ink";
export function Composer({ value, generating: _generating }: { value: string; generating: boolean }) {
  return (
    <Box flexDirection="column">
      <Text>{"─".repeat(process.stdout.columns ?? 80)}</Text>
      <Box>
        <Text color="green">›</Text>
        <Text> {value}<Text color="cyan">█</Text></Text>
      </Box>
    </Box>
  );
}

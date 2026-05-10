/** In-place spinner → result card. Phase 1. */

import { Box, Text } from "ink";
import type { ToolCall } from "../state/slices/tools.js";
export function ToolCallCard({ tool }: { tool: ToolCall }) {
  const icon = tool.status === "done" ? "✓" : tool.status === "failed" ? "✕" : "⟳";
  return (
    <Box>
      <Text dimColor>{icon} {tool.toolName} ⏱{tool.elapsedMs}ms</Text>
    </Box>
  );
}

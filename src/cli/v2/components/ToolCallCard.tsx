/** In-place spinner → result card. Phase 1. */

import { useState, useEffect } from "react";
import { Box, Text } from "ink";
import type { ToolCall } from "../state/slices/tools.js";

const SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

export interface ToolCallCardProps {
  tool: ToolCall;
}

export function ToolCallCard({ tool }: ToolCallCardProps) {
  const [frame, setFrame] = useState(0);

  useEffect(() => {
    if (tool.status !== "running" && tool.status !== "pending") return;
    const t = setInterval(() => setFrame((f) => (f + 1) % SPINNER.length), 80);
    return () => clearInterval(t);
  }, [tool.status]);

  return (
    <Box paddingLeft={2}>
      {tool.status === "running" || tool.status === "pending" ? (
        <Text dimColor>
          {SPINNER[frame]} {tool.toolName}
          {tool.elapsedMs > 0 ? (
            <Text color="gray"> ⏱{tool.elapsedMs}ms</Text>
          ) : null}
          {tool.progressMessage ? (
            <Text dimColor> {tool.progressMessage}</Text>
          ) : null}
        </Text>
      ) : tool.status === "done" ? (
        <Text dimColor>
          ✓ {tool.toolName} <Text color="gray">⏱{tool.elapsedMs}ms</Text>
        </Text>
      ) : (
        <Text color="red">
          ✕ {tool.toolName}: {tool.error ?? "unknown error"}
        </Text>
      )}
    </Box>
  );
}

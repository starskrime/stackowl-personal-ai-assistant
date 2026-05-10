/**
 * ToolCallCard — inline tool status card.
 *
 * States:
 *   running  · toolName  progress msg   2.3s    ← StackOwl amber spinner + name
 *   done     └ toolName  ✓ 2.3s                 ← dim with green checkmark
 *   failed   └ toolName  ✗ error message         ← red error
 */

import { useState, useEffect } from "react";
import { Box, Text } from "ink";
import type { ToolCall } from "../state/slices/tools.js";
import {
  STACKOWL_SPINNER,
  SPINNER_AMBER,
  SPINNER_INTERVAL_MS,
} from "./spinner.js";

function fmtTime(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export interface ToolCallCardProps {
  tool: ToolCall;
}

export function ToolCallCard({ tool }: ToolCallCardProps) {
  const [frame, setFrame] = useState(0);

  useEffect(() => {
    if (tool.status !== "running" && tool.status !== "pending") return;
    const t = setInterval(
      () => setFrame((f) => (f + 1) % STACKOWL_SPINNER.length),
      SPINNER_INTERVAL_MS,
    );
    return () => clearInterval(t);
  }, [tool.status]);

  if (tool.status === "running" || tool.status === "pending") {
    return (
      <Box paddingLeft={2}>
        <Text color={SPINNER_AMBER}>{STACKOWL_SPINNER[frame]} </Text>
        <Text bold>{tool.toolName}</Text>
        {tool.progressMessage ? (
          <Text dimColor> {tool.progressMessage}</Text>
        ) : null}
        {tool.elapsedMs > 0 ? (
          <Text dimColor> {fmtTime(tool.elapsedMs)}</Text>
        ) : null}
      </Box>
    );
  }

  if (tool.status === "done") {
    return (
      <Box paddingLeft={2}>
        <Text dimColor>└ {tool.toolName} </Text>
        <Text color="green">✓</Text>
        <Text dimColor> {fmtTime(tool.elapsedMs)}</Text>
      </Box>
    );
  }

  return (
    <Box paddingLeft={2}>
      <Text dimColor>└ {tool.toolName} </Text>
      <Text color="red">✗ {tool.error ?? "error"}</Text>
    </Box>
  );
}

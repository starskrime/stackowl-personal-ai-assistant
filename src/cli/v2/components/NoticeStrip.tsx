/**
 * NoticeStrip — single-line dim pill for instincts/perches/skills that fired.
 *
 *   ∷ [instinct] confidence nudge applied        ← info/warn
 *   ✕ [mcp]      connection failed               ← error (red)
 */

import { Box, Text } from "ink";
import type { Notice } from "../state/slices/heartbeat.js";

export interface NoticeStripProps {
  notice: Notice;
}

export function NoticeStrip({ notice }: NoticeStripProps) {
  if (notice.severity === "error") {
    return (
      <Box paddingLeft={2}>
        <Text color="red">✕ </Text>
        <Text color="red" dimColor>[{notice.source}]  </Text>
        <Text color="red">{notice.text}</Text>
      </Box>
    );
  }
  return (
    <Box paddingLeft={2}>
      <Text dimColor>∷ [{notice.source}]  {notice.text}</Text>
    </Box>
  );
}

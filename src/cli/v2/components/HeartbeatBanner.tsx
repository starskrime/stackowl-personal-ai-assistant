/** Bordered "knock" card for unsolicited owl messages. Phase 1. */

import { Box, Text } from "ink";
import type { HeartbeatMessage } from "../state/slices/heartbeat.js";

export interface HeartbeatBannerProps {
  msg: HeartbeatMessage;
}

export function HeartbeatBanner({ msg }: HeartbeatBannerProps) {
  return (
    <Box borderStyle="single" borderColor="magenta" paddingX={1}>
      <Text>
        🔔 <Text dimColor>[unsolicited · {msg.owlName}]</Text> {msg.text}
      </Text>
    </Box>
  );
}

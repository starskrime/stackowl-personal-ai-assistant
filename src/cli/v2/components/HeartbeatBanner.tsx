/** Bordered "knock" card for unsolicited owl messages. Phase 2. */

import { Box, Text } from "ink";
import type { HeartbeatMessage } from "../state/slices/heartbeat.js";
export function HeartbeatBanner({ msg }: { msg: HeartbeatMessage }) {
  return (
    <Box borderStyle="single" borderColor="magenta" paddingX={1}>
      <Text>🔔 <Text dimColor>[unsolicited · {msg.owlName}]</Text> {msg.text}</Text>
    </Box>
  );
}

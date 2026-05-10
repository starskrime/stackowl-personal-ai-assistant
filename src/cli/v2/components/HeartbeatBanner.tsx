/**
 * HeartbeatBanner — bordered "knock" card for unsolicited owl proactive messages.
 *
 * Layout:
 *   ╭─ 🔔 [unsolicited · OwlName] ──────────╮
 *   │  Message text here                      │
 *   ╰─────────────────────────────────────────╯
 *
 * Distinct from solicited chat turns so users immediately recognise a
 * proactive reach-out from the owl rather than a reply to their question.
 */

import { Box, Text } from "ink";
import type { HeartbeatMessage } from "../state/slices/heartbeat.js";

export interface HeartbeatBannerProps {
  msg: HeartbeatMessage;
}

export function HeartbeatBanner({ msg }: HeartbeatBannerProps) {
  return (
    <Box
      borderStyle="round"
      borderColor="magenta"
      flexDirection="column"
      paddingX={1}
      marginY={0}
    >
      {/* Header row: emoji + [unsolicited · OwlName] */}
      <Box>
        <Text bold color="magenta">
          {msg.owlEmoji ? `${msg.owlEmoji} ` : "🔔 "}
        </Text>
        <Text dimColor>[unsolicited · {msg.owlName}]</Text>
      </Box>
      {/* Body: the message text */}
      <Box paddingTop={0}>
        <Text wrap="wrap">{msg.text}</Text>
      </Box>
    </Box>
  );
}

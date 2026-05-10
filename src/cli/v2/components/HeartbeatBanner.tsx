/**
 * HeartbeatBanner — bordered card for unsolicited owl proactive messages.
 *
 *   ╭─────────────────────────────────────────╮
 *   │  🔔 Hoots  unsolicited                  │
 *   │                                         │
 *   │  Your reminder text here                │
 *   ╰─────────────────────────────────────────╯
 *
 * Purple (#A78BFA) border distinguishes proactive messages from all solicited turns.
 */

import { Box, Text } from "ink";
import type { HeartbeatMessage } from "../state/slices/heartbeat.js";

const HEARTBEAT_PURPLE = "#A78BFA";

export interface HeartbeatBannerProps {
  msg: HeartbeatMessage;
}

export function HeartbeatBanner({ msg }: HeartbeatBannerProps) {
  const emoji = msg.owlEmoji ?? "🔔";
  return (
    <Box
      borderStyle="round"
      borderColor={HEARTBEAT_PURPLE}
      flexDirection="column"
      paddingX={1}
      paddingY={0}
      marginTop={0}
      marginBottom={1}
    >
      <Box>
        <Text>{emoji} </Text>
        <Text bold color={HEARTBEAT_PURPLE}>{msg.owlName}</Text>
        <Text dimColor>  unsolicited</Text>
      </Box>
      <Box marginTop={0} paddingLeft={0}>
        <Text wrap="wrap">{msg.text}</Text>
      </Box>
    </Box>
  );
}

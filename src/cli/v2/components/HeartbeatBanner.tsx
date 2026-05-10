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
import { useTheme } from "../providers/ThemeProvider.js";

export interface HeartbeatBannerProps {
  msg: HeartbeatMessage;
}

export function HeartbeatBanner({ msg }: HeartbeatBannerProps) {
  const { colors } = useTheme();
  const emoji = msg.owlEmoji ?? "🔔";
  return (
    <Box
      borderStyle="round"
      borderColor={colors.heartbeat}
      flexDirection="column"
      paddingX={1}
      paddingY={0}
      marginTop={0}
      marginBottom={1}
    >
      <Box>
        <Text>{emoji} </Text>
        <Text bold color={colors.heartbeat}>{msg.owlName}</Text>
        <Text dimColor>  unsolicited</Text>
      </Box>
      <Box marginTop={0} paddingLeft={0}>
        <Text wrap="wrap">{msg.text}</Text>
      </Box>
    </Box>
  );
}

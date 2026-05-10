/**
 * ChatScreen — the default inline-scroll chat surface.
 *
 * Phase 0 stub. Full implementation in Phase 1.
 *
 * Layout:
 *   <Transcript />        ← <Static> committed turns, native scrollback
 *   [heartbeat banners]   ← HeartbeatBanner per unsolicited message
 *   [notice strips]       ← NoticeStrip for instincts/perches/skills
 *   <LiveTurn />          ← streaming live region (token.delta)
 *   separator
 *   <Composer />          ← multi-line input, history, paste
 *   <ShortcutsBar />      ← footer: owl · model · tokens · cost
 */


import { Box, Text } from "ink";

export function ChatScreen() {
  return (
    <Box flexDirection="column">
      <Text dimColor>StackOwl TUI v2 — Phase 0 scaffold</Text>
      <Text dimColor>Chat surface coming in Phase 1.</Text>
    </Box>
  );
}

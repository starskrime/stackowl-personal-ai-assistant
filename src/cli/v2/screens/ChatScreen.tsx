/**
 * ChatScreen — inline-scroll chat surface.
 *
 * Ink rendering order (Static items go into the terminal scroll buffer in order):
 *
 *   <Static items={['header']}>   ← logo + green rules, committed ONCE at startup
 *   <Transcript />                ← committed turns, appended below the header
 *   [activity: LiveTurn, panels]  ← dynamic, always visible at current position
 *   <Composer />                  ← dynamic input, always visible
 */

import { Box, Static, Text } from "ink";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { Frame } from "../components/Frame.js";
import { EmptyState } from "../components/EmptyState.js";
import { Transcript } from "../components/Transcript.js";
import { HeartbeatBanner } from "../components/HeartbeatBanner.js";
import { NoticeStrip } from "../components/NoticeStrip.js";
import { LiveTurn } from "../components/LiveTurn.js";
import { Composer } from "../components/Composer.js";
import { CommandPalette } from "../components/CommandPalette.js";
import { PanelHost } from "../panels/PanelHost.js";
import { globalBridge } from "../events/bridge.js";

export interface ChatScreenProps {
  onSubmit: (text: string) => void;
}

const GREEN_RULE = (
  <Box borderStyle="single" borderTop={true} borderBottom={false} borderLeft={false} borderRight={false} borderColor="green" />
);

const HEADER_ITEMS = ["header"];

export function ChatScreen({ onSubmit }: ChatScreenProps) {
  const turns        = useUiStore((s) => s.turns);
  const liveTurn     = useUiStore((s) => s.liveTurn);
  const toolCalls    = useUiStore((s) => s.toolCalls);
  const heartbeats   = useUiStore((s) => s.heartbeats);
  const notices      = useUiStore((s) => s.notices);
  const generating   = useUiStore((s) => s.generating);
  const showHelp     = useUiStore((s) => s.showHelp);
  const panelFocus   = useUiStore((s) => s.panelFocus);

  const unreadHeartbeats = heartbeats.filter((msg) => !msg.read).slice(-3);
  const recentNotices    = notices.slice(-3);
  const activeCalls      = Array.from(toolCalls.values());

  return (
    <Box flexDirection="column">
      {/* Header committed once as static — stays at top of scroll buffer, messages appear below it */}
      <Static items={HEADER_ITEMS}>
        {(item: string) => (
          <Box key={item} flexDirection="column">
            {GREEN_RULE}
            <Frame>
              <EmptyState />
            </Frame>
            {GREEN_RULE}
          </Box>
        )}
      </Static>

      {/* Transcript: committed turns appended to scroll buffer below the header */}
      <Frame>
        <Transcript turns={turns} />
      </Frame>

      {/* Dynamic section — always visible at the current cursor position */}
      <Frame>
        {unreadHeartbeats.map((msg) => (
          <HeartbeatBanner key={msg.id} msg={msg} />
        ))}
        {recentNotices.map((n) => (
          <NoticeStrip key={n.id} notice={n} />
        ))}
        <LiveTurn turn={liveTurn} toolCalls={activeCalls} />
        {showHelp && <CommandPalette onClose={() => globalBridge.dismissHelpView()} />}
        <PanelHost />
      </Frame>

      {/* Input area — full terminal width */}
      <Box
        flexDirection="column"
        borderStyle="single"
        borderTop={true}
        borderBottom={true}
        borderLeft={false}
        borderRight={false}
        borderColor="green"
      >
        <Composer
          onSubmit={onSubmit}
          disabled={generating || showHelp || panelFocus === "panel"}
        />
      </Box>
      <Box paddingLeft={1}>
        <Text dimColor>use Shift+Tab to change current owl</Text>
      </Box>
    </Box>
  );
}

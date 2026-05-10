/**
 * ChatScreen — the default inline-scroll chat surface.
 *
 * Layout (Ink rendering model):
 *   <Transcript />      ← Static: committed turns scroll into terminal buffer above
 *   ── green ──────     ← always visible: top of the persistent header block
 *   <EmptyState />      ← always visible: logo + subtitle
 *   ── green ──────     ← always visible: closes the header block
 *   [heartbeat banners]
 *   [notice strips]
 *   <LiveTurn />
 *   <CommandPalette />  ← /help overlay
 *   <PanelHost />       ← inline panels (/memory, /mcp, …)
 *   ── green ──────     ← top of input border (full terminal width)
 *   <Composer />
 *   ── green ──────     ← bottom of input border
 */

import { Box, Text } from "ink";
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

export function ChatScreen({ onSubmit }: ChatScreenProps) {
  const turns        = useUiStore((s) => s.turns);
  const liveTurn     = useUiStore((s) => s.liveTurn);
  const toolCalls    = useUiStore((s) => s.toolCalls);
  const heartbeats   = useUiStore((s) => s.heartbeats);
  const notices      = useUiStore((s) => s.notices);
  const generating   = useUiStore((s) => s.generating);
  const showHelp          = useUiStore((s) => s.showHelp);
  const panelFocus        = useUiStore((s) => s.panelFocus);

  const unreadHeartbeats = heartbeats.filter((msg) => !msg.read).slice(-3);
  const recentNotices    = notices.slice(-3);
  const activeCalls      = Array.from(toolCalls.values());

  return (
    <Box flexDirection="column">
      {/* Transcript: Static — committed turns scroll into the terminal buffer above the header */}
      <Frame>
        <Transcript turns={turns} />
      </Frame>

      {/* Persistent header — always visible at the top of the current viewport */}
      {GREEN_RULE}
      <Frame>
        <EmptyState />
      </Frame>
      {GREEN_RULE}

      {/* Activity area */}
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

      {/* Input area — outside Frame so green lines span full terminal width */}
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

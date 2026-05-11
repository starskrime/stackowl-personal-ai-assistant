/**
 * ChatScreen — inline-scroll chat surface.
 *
 * The persistent header (green rules + STACKOWL logo) is written to stdout
 * once in startV2() before Ink starts. Ink renders below it and never touches it.
 *
 * Ink layout:
 *   <Transcript />   ← Static: committed turns appended below the header
 *   [activity]       ← dynamic: LiveTurn, panels, heartbeats
 *   <Composer />     ← dynamic: always visible at current cursor position
 */

import { Box, Text } from "ink";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { Frame } from "../components/Frame.js";
import { Transcript } from "../components/Transcript.js";
import { HeartbeatBanner } from "../components/HeartbeatBanner.js";
import { NoticeStrip } from "../components/NoticeStrip.js";
import { LiveTurn } from "../components/LiveTurn.js";
import { ThinkingIndicator } from "../components/ThinkingIndicator.js";
import { OwlAvatar } from "../components/OwlAvatar.js";
import { Composer } from "../components/Composer.js";
import { CommandPalette } from "../components/CommandPalette.js";
import { PanelHost } from "../panels/PanelHost.js";
import { globalBridge } from "../events/bridge.js";

export interface ChatScreenProps {
  onSubmit: (text: string) => void;
}

export function ChatScreen({ onSubmit }: ChatScreenProps) {
  const turns         = useUiStore((s) => s.turns);
  const liveTurn      = useUiStore((s) => s.liveTurn);
  const toolCalls     = useUiStore((s) => s.toolCalls);
  const heartbeats    = useUiStore((s) => s.heartbeats);
  const notices       = useUiStore((s) => s.notices);
  const generating    = useUiStore((s) => s.generating);
  const showHelp      = useUiStore((s) => s.showHelp);
  const panelFocus    = useUiStore((s) => s.panelFocus);
  const activeOwlName  = useUiStore((s) => s.activeOwlName);
  const activeOwlEmoji = useUiStore((s) => s.activeOwlEmoji);

  const unreadHeartbeats = heartbeats.filter((msg) => !msg.read).slice(-3);
  const recentNotices    = notices.slice(-3);
  const activeCalls      = Array.from(toolCalls.values());

  return (
    <Box flexDirection="column">
      {/* Committed turns — appended to scroll buffer below the pre-rendered header */}
      <Frame>
        <Transcript turns={turns} />
      </Frame>

      {/* Dynamic activity area */}
      <Frame>
        {unreadHeartbeats.map((msg) => (
          <HeartbeatBanner key={msg.id} msg={msg} />
        ))}
        {recentNotices.map((n) => (
          <NoticeStrip key={n.id} notice={n} />
        ))}
        {/* Show owl + thinking indicator before the live turn object exists */}
        {generating && !liveTurn && (
          <Box flexDirection="column" marginBottom={1}>
            <OwlAvatar emoji={activeOwlEmoji} name={activeOwlName} />
            <Box paddingLeft={2}>
              <ThinkingIndicator />
            </Box>
          </Box>
        )}
        <LiveTurn turn={liveTurn} toolCalls={activeCalls} />
        {showHelp && <CommandPalette onClose={() => globalBridge.dismissHelpView()} />}
        <PanelHost />
      </Frame>

      {/* Input — full terminal width, always visible */}
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

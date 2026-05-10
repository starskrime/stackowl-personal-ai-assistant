/**
 * ChatScreen — the default inline-scroll chat surface.
 *
 * Layout:
 *   <TopBar />            ← brand bar + git branch
 *   <Frame>               ← max-width container with gutters
 *     <Transcript />      ← committed turns (native scrollback)
 *     [heartbeat banners]
 *     [notice strips]
 *     <LiveTurn />
 *     <CommandPalette />  ← /help overlay
 *     <PanelHost />       ← inline panels (/memory, /mcp, …)
 *   </Frame>
 *   <InputArea />         ← full-width green top/bottom lines, outside Frame gutters
 *     <Composer />        ← input + hint row
 *     <StatusBar />       ← owl · model · tokens · cost
 *     <ShortcutsBar />    ← keyboard hints (Shift+Tab = cycle owl)
 */

import { Box } from "ink";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { Frame } from "../components/Frame.js";
import { TopBar } from "../components/TopBar.js";
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
      <TopBar />
      <Frame>
        {turns.length === 0 && !liveTurn ? <EmptyState /> : <Transcript turns={turns} />}
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
    </Box>
  );
}

/**
 * ChatScreen — the default inline-scroll chat surface.
 *
 * Layout:
 *   <Transcript />        ← <Static> committed turns, native scrollback
 *   [heartbeat banners]   ← HeartbeatBanner per unread unsolicited message (last 3)
 *   [notice strips]       ← NoticeStrip for instincts/perches/skills (last 3)
 *   <LiveTurn />          ← streaming live region (token.delta)
 *   <Composer />          ← bordered input box
 *   <StatusBar />         ← dim pipe-separated footer line (owl, model, tokens, cost)
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
import { StatusBar } from "../components/StatusBar.js";
import { CommandPalette } from "../components/CommandPalette.js";
import { SkillsOverlay } from "../components/SkillsOverlay.js";
import { McpOverlay } from "../components/McpOverlay.js";
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
  const showHelp     = useUiStore((s) => s.showHelp);
  const showSkillsOverlay = useUiStore((s) => s.showSkillsOverlay);
  const showMcpOverlay    = useUiStore((s) => s.showMcpOverlay);

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
        {showSkillsOverlay && <SkillsOverlay />}
        {showMcpOverlay && <McpOverlay />}
        <Composer
          onSubmit={onSubmit}
          disabled={generating || showHelp || showSkillsOverlay || showMcpOverlay}
        />
        <StatusBar />
      </Frame>
    </Box>
  );
}

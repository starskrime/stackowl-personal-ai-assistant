/**
 * ChatScreen — the default inline-scroll chat surface.
 *
 * Layout:
 *   <Transcript />        ← <Static> committed turns, native scrollback
 *   [heartbeat banners]   ← HeartbeatBanner per unread unsolicited message (last 3)
 *   [notice strips]       ← NoticeStrip for instincts/perches/skills (last 3)
 *   <LiveTurn />          ← streaming live region (token.delta)
 *   <Composer />          ← multi-line input, history, paste
 *   <ShortcutsBar />      ← footer: owl · model · tokens · cost
 */

import { Box } from "ink";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { Transcript } from "../components/Transcript.js";
import { HeartbeatBanner } from "../components/HeartbeatBanner.js";
import { NoticeStrip } from "../components/NoticeStrip.js";
import { LiveTurn } from "../components/LiveTurn.js";
import { Composer } from "../components/Composer.js";
import { ShortcutsBar } from "../components/ShortcutsBar.js";
import { CommandPalette } from "../components/CommandPalette.js";
import { SkillsOverlay } from "../components/SkillsOverlay.js";
import { McpOverlay } from "../components/McpOverlay.js";
import { globalBridge } from "../events/bridge.js";

export interface ChatScreenProps {
  onSubmit: (text: string) => void;
}

export function ChatScreen({ onSubmit }: ChatScreenProps) {
  const turns = useUiStore((s) => s.turns);
  const liveTurn = useUiStore((s) => s.liveTurn);
  const toolCalls = useUiStore((s) => s.toolCalls);
  const heartbeats = useUiStore((s) => s.heartbeats);
  const notices = useUiStore((s) => s.notices);
  const generating = useUiStore((s) => s.generating);
  const activeOwlName = useUiStore((s) => s.activeOwlName);
  const activeOwlEmoji = useUiStore((s) => s.activeOwlEmoji);
  const activeModel = useUiStore((s) => s.activeModel);
  const totalTokens = useUiStore((s) => s.totalTokens);
  const totalCostUsd = useUiStore((s) => s.totalCostUsd);
  const showHelp = useUiStore((s) => s.showHelp);
  const showSkillsOverlay = useUiStore((s) => s.showSkillsOverlay);
  const showMcpOverlay = useUiStore((s) => s.showMcpOverlay);

  const unreadHeartbeats = heartbeats.filter((msg) => !msg.read).slice(-3);
  const recentNotices = notices.slice(-3);
  const activeCalls = Array.from(toolCalls.values());

  return (
    <Box flexDirection="column">
      <Transcript turns={turns} />
      {unreadHeartbeats.map((msg) => (
        <HeartbeatBanner key={msg.id} msg={msg} />
      ))}
      {recentNotices.map((n) => (
        <NoticeStrip key={n.id} notice={n} />
      ))}
      <LiveTurn turn={liveTurn} toolCalls={activeCalls} />
      {/* Inline overlays — rendered above the Composer, dismissed by Escape */}
      {showHelp && <CommandPalette onClose={() => globalBridge.dismissHelpView()} />}
      {showSkillsOverlay && <SkillsOverlay />}
      {showMcpOverlay && <McpOverlay />}
      <Composer onSubmit={onSubmit} disabled={generating || showHelp || showSkillsOverlay || showMcpOverlay} />
      <ShortcutsBar
        owlEmoji={activeOwlEmoji}
        owlName={activeOwlName}
        model={activeModel}
        generating={generating}
        totalTokens={totalTokens}
        totalCostUsd={totalCostUsd}
      />
    </Box>
  );
}

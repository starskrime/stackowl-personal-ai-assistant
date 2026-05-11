/**
 * ChatScreen — alt-screen chat surface.
 *
 * Alt-screen mode (\x1B[?1049h) is entered in startV2() so the entire UI
 * re-renders on terminal resize. There is no native scrollback; instead we
 * maintain a viewport window over the turn history and wire PageUp/PageDown
 * to scroll it.
 *
 * Layout:
 *   <Header />       ← green rules + STACKOWL logo + tagline (adaptive)
 *   [messaging area] ← paddingX={2}, viewport slice of committed turns
 *   <Composer />     ← full-width, always visible
 *   [footer hint]
 */

import { useState, useEffect, useMemo } from "react";
import { Box, Text, useInput } from "ink";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { Header } from "../components/Header.js";
import { Transcript } from "../components/Transcript.js";
import { ExitConfirmDialog } from "../components/ExitConfirmDialog.js";
import { HeartbeatBanner } from "../components/HeartbeatBanner.js";
import { NoticeStrip } from "../components/NoticeStrip.js";
import { LiveTurn } from "../components/LiveTurn.js";
import { ThinkingIndicator } from "../components/ThinkingIndicator.js";
import { OwlAvatar } from "../components/OwlAvatar.js";
import { Composer } from "../components/Composer.js";
import { CommandPalette } from "../components/CommandPalette.js";
import { PanelHost } from "../panels/PanelHost.js";
import { globalBridge } from "../events/bridge.js";
import { useTerminalRows } from "../input/useTerminalRows.js";

export interface ChatScreenProps {
  onSubmit: (text: string) => void;
}

/** Approximate header height: 2 rules + 6 logo lines + tagline = 9 rows. */
const HEADER_ROWS = 9;
/** Approximate composer + footer height: border-top + input + border-bottom + hint = 4 rows. */
const CHROME_ROWS = HEADER_ROWS + 4;

export function ChatScreen({ onSubmit }: ChatScreenProps) {
  const turns         = useUiStore((s) => s.turns);
  const liveTurn      = useUiStore((s) => s.liveTurn);
  const toolCalls     = useUiStore((s) => s.toolCalls);
  const heartbeats    = useUiStore((s) => s.heartbeats);
  const notices       = useUiStore((s) => s.notices);
  const generating    = useUiStore((s) => s.generating);
  const showHelp         = useUiStore((s) => s.showHelp);
  const panelFocus       = useUiStore((s) => s.panelFocus);
  const exitConfirmOpen  = useUiStore((s) => s.exitConfirmOpen);
  const activeOwlName    = useUiStore((s) => s.activeOwlName);
  const activeOwlEmoji   = useUiStore((s) => s.activeOwlEmoji);
  const liveMemoryCount  = useUiStore((s) => s.liveMemoryCount);

  const rows = useTerminalRows();

  // Viewport: 0 = follow latest. Positive = scrolled back by that many turns.
  const [viewportOffset, setViewportOffset] = useState(0);

  // Auto-follow to latest when a new turn is committed.
  useEffect(() => { setViewportOffset(0); }, [turns.length]);

  // Estimate how many turns fit on screen. Each turn averages ~3 rows.
  const windowSize = Math.max(1, Math.floor((rows - CHROME_ROWS) / 3));

  const tailIdx  = turns.length - viewportOffset;
  const startIdx = Math.max(0, tailIdx - windowSize);

  // Stable reference: only changes when committed turns list or scroll position changes,
  // not on every token.delta. Combined with React.memo(Transcript) this prevents
  // Transcript from re-rendering during streaming.
  const visibleTurns = useMemo(
    () => turns.slice(startIdx, Math.max(0, tailIdx)),
    [turns, startIdx, tailIdx],
  );

  const scrolledBack = viewportOffset > 0;
  const hiddenAbove  = startIdx;
  const hiddenBelow  = viewportOffset;

  // PageUp / PageDown scrolling — only when no overlay / panel is active.
  useInput((_input, key) => {
    if (showHelp || panelFocus === "panel" || exitConfirmOpen) return;
    if (key.pageUp) {
      setViewportOffset((prev) => Math.min(prev + Math.max(1, windowSize - 2), turns.length - 1));
    } else if (key.pageDown) {
      setViewportOffset((prev) => Math.max(0, prev - Math.max(1, windowSize - 2)));
    } else if (key.escape && scrolledBack) {
      setViewportOffset(0);
    }
  });

  const unreadHeartbeats = heartbeats.filter((msg) => !msg.read).slice(-3);
  const recentNotices    = notices.slice(-3);
  const activeCalls      = Array.from(toolCalls.values());

  if (exitConfirmOpen) {
    return <ExitConfirmDialog />;
  }

  return (
    // height={rows} pins the layout to the terminal height so Header + Composer
    // are never evicted when transcript content grows past the screen boundary.
    <Box flexDirection="column" height={rows}>
      <Header />

      {/* Middle region: grows to fill remaining space, clips overflow at bottom */}
      <Box flexDirection="column" flexGrow={1} flexShrink={1} overflow="hidden">
        {/* Scroll indicator — shown when not at the bottom */}
        {hiddenAbove > 0 && (
          <Box justifyContent="center">
            <Text dimColor>↑ {hiddenAbove} earlier {hiddenAbove === 1 ? "turn" : "turns"} — PageUp to scroll</Text>
          </Box>
        )}

        {/* Messaging area — 2-col gutter each side */}
        <Box flexDirection="column" paddingX={2} flexGrow={1} flexShrink={1} overflow="hidden">
          <Transcript turns={visibleTurns} />
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
          <LiveTurn turn={liveTurn} toolCalls={activeCalls} memoryCount={liveMemoryCount} />
          {showHelp && <CommandPalette onClose={() => globalBridge.dismissHelpView()} />}
          <PanelHost />
        </Box>

        {/* Scroll-to-bottom hint when scrolled back */}
        {hiddenBelow > 0 && (
          <Box justifyContent="center">
            <Text dimColor>↓ {hiddenBelow} newer {hiddenBelow === 1 ? "turn" : "turns"} — PageDown or Esc to follow</Text>
          </Box>
        )}
      </Box>

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

import { Box, Text } from "ink";
import { basename } from "path";
import { useTheme } from "../providers/ThemeProvider.js";
import { useTerminalCols } from "../input/useTerminalCols.js";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { useGitBranch } from "../input/useGitBranch.js";

export function TopBar() {
  const { colors, glyphs } = useTheme();
  const cols = useTerminalCols();

  const owlEmoji  = useUiStore((s) => s.activeOwlEmoji);
  const owlName   = useUiStore((s) => s.activeOwlName);
  const model     = useUiStore((s) => s.activeModel);
  const mode      = useUiStore((s) => s.mode);
  const sessionId = useUiStore((s) => s.activeSessionId ?? "");

  const branch = useGitBranch();
  const cwd    = basename(process.cwd());
  const sessionShort = sessionId ? sessionId.slice(-6) : "";

  // Divider spans full terminal width
  const divider = glyphs.divider.repeat(cols);

  // Rough truncation budget for Row 2 (2 chars paddingX on each side = 4 total)
  const budget = cols - 4;
  const cwdText      = cwd;
  const branchText   = branch  ? ` · ${branch}`        : "";
  const sessionText  = sessionShort ? ` · §${sessionShort}` : "";
  const modeText     = mode !== "chat" ? ` [${mode}]` : "";

  // Decide what to show based on remaining width (omit rightmost fields first)
  let usedWidth = cwdText.length;
  const showBranch  = branch     && (usedWidth + branchText.length + 3  <= budget);
  if (showBranch) usedWidth += branchText.length;
  const showSession = sessionShort && (usedWidth + sessionText.length + 3 <= budget);

  return (
    <Box flexDirection="column">
      {/* Row 1: identity */}
      <Box paddingX={1}>
        <Text bold color={colors.brand}>{owlEmoji} {owlName}</Text>
        {model ? <Text dimColor> · {model}</Text> : null}
      </Box>

      {/* Row 2: context */}
      <Box paddingX={1}>
        <Text dimColor>{cwdText}</Text>
        {showBranch   ? <Text dimColor>{branchText}</Text>  : null}
        {showSession  ? <Text dimColor>{sessionText}</Text> : null}
        {mode !== "chat" ? <Text color={colors.accent}>{modeText}</Text> : null}
      </Box>

      {/* Divider */}
      <Text dimColor>{divider}</Text>
    </Box>
  );
}

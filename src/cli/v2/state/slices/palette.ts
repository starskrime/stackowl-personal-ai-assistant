/**
 * palette.ts — state for CommandPalette data (owls, skills, MCP servers).
 *
 * These lists are populated asynchronously by the CliV2Adapter on startup
 * and refreshed when the relevant slash command is opened.
 */

import type { UiState } from "../store.js";
import type { UiEvent, OwlSummaryRecord, SkillSummaryRecord, McpServerRecord } from "../../events/UiEvent.js";

export interface PaletteState {
  availableOwls: OwlSummaryRecord[];
  installedSkills: SkillSummaryRecord[];
  mcpServers: McpServerRecord[];
}

export const initialPaletteState: PaletteState = {
  availableOwls: [],
  installedSkills: [],
  mcpServers: [],
};

export function applyPaletteEvent(state: UiState, event: UiEvent): UiState {
  switch (event.kind) {
    case "owls.loaded":
      return { ...state, availableOwls: event.owls };
    case "skills.loaded":
      return { ...state, installedSkills: event.skills };
    case "mcp.loaded":
      return { ...state, mcpServers: event.servers };
    default:
      return state;
  }
}

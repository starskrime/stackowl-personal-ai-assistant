import { globalBridge } from "../events/bridge.js";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { Panel } from "./Panel.js";
import type { PanelProps } from "./Panel.js";

export function PanelHost() {
  const activePanel = useUiStore((s) => s.activePanel);
  const panelFocus  = useUiStore((s) => s.panelFocus);

  if (!activePanel) return null;

  const props = activePanel.props as Omit<PanelProps, "onDismiss" | "isActive">;

  return (
    <Panel
      {...props}
      isActive={panelFocus === "panel"}
      onDismiss={() => globalBridge.closePanel()}
    />
  );
}

/**
 * Subscribes to the global UiBridge on mount and pipes events through the
 * reducer into the Zustand store. Unmounts cleanly.
 */

import React, { useEffect } from "react";
import { globalBridge } from "../events/bridge.js";
import { reduce } from "../events/reducer.js";
import { applyToStore } from "../state/store.js";

export function EventBusProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    const unsub = globalBridge.subscribe((event) => {
      applyToStore((state) => reduce(state, event));
    });
    return unsub;
  }, []);

  return <>{children}</>;
}

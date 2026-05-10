import React, { createContext, useContext } from "react";
import { useStore } from "zustand/react";
import { uiStore, type UiState } from "../state/store.js";

// The singleton vanilla store is used as the context value so that bridge /
// adapter code (which cannot use React context) continues to target the same
// store via applyToStore().
const UiStoreContext = createContext<typeof uiStore | null>(null);

export function UiStoreProvider({ children }: { children: React.ReactNode }) {
  return (
    <UiStoreContext.Provider value={uiStore}>
      {children}
    </UiStoreContext.Provider>
  );
}

export function useUiStore<T>(selector: (state: UiState) => T): T {
  const store = useContext(UiStoreContext);
  if (!store) throw new Error("useUiStore must be used within UiStoreProvider");
  return useStore(store, selector);
}

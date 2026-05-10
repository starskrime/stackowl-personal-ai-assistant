import React, { createContext, useContext, useSyncExternalStore } from "react";
import { uiStore, type UiState } from "../state/store.js";

const UiStoreContext = createContext(uiStore);

export function UiStoreProvider({ children }: { children: React.ReactNode }) {
  return (
    <UiStoreContext.Provider value={uiStore}>
      {children}
    </UiStoreContext.Provider>
  );
}

export function useUiStore<T>(selector: (state: UiState) => T): T {
  const store = useContext(UiStoreContext);
  return useSyncExternalStore(store.subscribe, () => selector(store.getState()));
}

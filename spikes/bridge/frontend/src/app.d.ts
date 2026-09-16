// Ambient window augmentation for the B5 check's own test seam
// (`window.__cspCheckRendererBackend`, set by App.svelte's onMount and read
// by check.py's Playwright step). Kept as a standalone ambient .d.ts rather
// than an inline `declare global` inside App.svelte's <script> block --
// svelte-check rejects a non-top-level ambient module/global declaration
// inside a component's script block.
import type { RendererBackend } from "./scene";

declare global {
  interface Window {
    __cspCheckRendererBackend?: () => RendererBackend | null;
  }
}

export {};

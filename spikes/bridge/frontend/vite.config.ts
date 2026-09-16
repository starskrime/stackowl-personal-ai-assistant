import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

// Story 1.5 (AD-36, AD-40): this kit's server (bridge_spike/server.py) mounts
// the committed build/ output at /csp-check/ -- `base` must match that mount
// point exactly, or the built HTML/asset URLs would resolve against the
// wrong path once served (they'd still satisfy `script-src 'self'` since the
// origin is the same, but would 404).
export default defineConfig({
  base: "/csp-check/",
  plugins: [svelte()],
  build: {
    outDir: "build",
    emptyOutDir: true,
    // Never inline the owl-mark SVG as a base64 data: URI: SVGLoader fetches
    // it at runtime (see scene.ts), and this kit's CSP `connect-src 'self'`
    // has no `data:` source -- only `img-src` does. Emitting it as a real
    // same-origin asset file keeps both the DOM <img> and the Three.js
    // fetch inside the enforced policy with no special-casing.
    assetsInlineLimit: 0,
  },
});

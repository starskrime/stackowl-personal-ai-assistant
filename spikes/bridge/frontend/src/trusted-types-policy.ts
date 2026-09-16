// Story 1.5 (AD-36, NFR22): the kit's ONE named Trusted Types policy,
// registered exactly once at this module's first import and shared by
// every sink under src/ that needs one:
//   - App.svelte's renderer-backend status label (Range.createContextualFragment)
//   - scene.ts's DOMParser.parseFromString patch ahead of SVGLoader (three's
//     SVGLoader is not itself Trusted-Types-aware and calls parseFromString
//     with a raw string, which `require-trusted-types-for 'script'` blocks)
//
// NFR22 counts createPolicy call sites across THIS KIT'S OWN front-end code
// (src/) -- Svelte's compiled runtime registers its own separate, unrelated
// policy (named "svelte-trusted-html", in
// svelte/internal/client/dom/reconciler.js) purely to mount static markup
// templates; that is framework-internal code this kit never wrote and
// check.py's static-source count deliberately never scans node_modules/.
export const trustedTypesPolicy: TrustedTypePolicy | null =
  typeof window !== "undefined" && window.trustedTypes
    ? window.trustedTypes.createPolicy("bridge-spike-frontend", {
        createHTML: (input: string) => input,
      })
    : null;

<script lang="ts">
  import { onMount } from "svelte";
  // Vite's `?url` suffix resolves to the built, hashed asset URL rather than
  // inlining the file -- this import (and App's own <img src=...> below) is
  // what makes "the DOM view is sourced from logo/stackowl-mark.svg" (AD-40)
  // literally true, never a hand-copied path string. scene.ts's SVGLoader
  // fetches the SAME url at runtime for the Three.js view.
  import owlMarkUrl from "../../../../logo/stackowl-mark.svg?url";
  import { createOwlScene, type RendererBackend } from "./scene";
  import { trustedTypesPolicy } from "./trusted-types-policy";

  let canvasEl: HTMLCanvasElement;
  let statusEl: HTMLParagraphElement;

  function escapeHtml(value: string): string {
    return value.replace(/[&<>"']/g, (ch) => {
      switch (ch) {
        case "&":
          return "&amp;";
        case "<":
          return "&lt;";
        case ">":
          return "&gt;";
        case '"':
          return "&quot;";
        default:
          return "&#39;";
      }
    });
  }

  // Genuine use of the shared policy (see ./trusted-types-policy): builds the
  // renderer-backend status label's markup through a Trusted-Types-gated
  // sink (Range.createContextualFragment) rather than `.innerHTML =`, which
  // this kit's own eslint.config.js bans outright (AD-36's "never relax the
  // policy" extends to never routing around our OWN lint tripwire either).
  // Removing the policy would make this line throw under the enforced CSP,
  // which is exactly what makes it load-bearing rather than dead code.
  function renderBackendStatus(backend: RendererBackend): void {
    const html = `Renderer backend: <strong data-testid="renderer-backend">${escapeHtml(backend)}</strong>`;
    const trusted = trustedTypesPolicy ? trustedTypesPolicy.createHTML(html) : html;
    const fragment = document.createRange().createContextualFragment(trusted as unknown as string);
    // Deliberate direct DOM write, not one of the three AD-36-banned
    // patterns: this is the one genuine sink (Range.createContextualFragment)
    // that exercises the Trusted Types policy above. The <p> template below
    // carries no reactive text of its own (a static mount anchor only), so
    // this is the sole owner of statusEl's children -- there is no risk of
    // it fighting the Svelte runtime.
    // eslint-disable-next-line svelte/no-dom-manipulating
    statusEl.replaceChildren(fragment);
  }

  // Plain-text write (never innerHTML/insertAdjacentHTML/{@html} -- textContent
  // isn't a Trusted-Types-gated sink) for the failure path below, so a
  // WebGPU-init or SVG-fetch rejection is visible on the page instead of
  // leaving the label stuck at "resolving…" forever with no diagnostic.
  function renderBackendError(error: unknown): void {
    const message = error instanceof Error ? error.message : String(error);
    // Same sole-owner reasoning as renderBackendStatus's disable above.
    // eslint-disable-next-line svelte/no-dom-manipulating
    statusEl.textContent = `Renderer backend: error (${message})`;
  }

  onMount(() => {
    const scene = createOwlScene(canvasEl, owlMarkUrl);
    window.__cspCheckRendererBackend = scene.getBackend;

    let cancelled = false;
    scene.ready
      .then((backend) => {
        if (!cancelled) {
          renderBackendStatus(backend);
        }
      })
      .catch((error: unknown) => {
        console.error("bridge-spike-frontend: owl scene failed to initialize", error);
        if (!cancelled) {
          renderBackendError(error);
        }
      });

    return () => {
      cancelled = true;
      delete window.__cspCheckRendererBackend;
      scene.dispose();
    };
  });
</script>

<main>
  <h1>Bridge spike &mdash; CSP / Trusted Types check</h1>
  <p>
    <img src={owlMarkUrl} width="48" height="48" alt="StackOwl mark" data-testid="owl-mark-img" />
    the StackOwl mark, rendered as a DOM &lt;img&gt; imported from <code>logo/stackowl-mark.svg</code>.
  </p>
  <canvas bind:this={canvasEl} width="320" height="240" data-testid="owl-scene-canvas"></canvas>
  <!-- Static mount anchor only -- no reactive text of its own. Its visible
       content is always supplied by renderBackendStatus()'s Trusted-Types
       fragment or renderBackendError()'s textContent write above, never by
       Svelte's own template renderer, so the two can never fight over
       ownership of this element's children. -->
  <p bind:this={statusEl} data-testid="renderer-backend-status"></p>
</main>

<style>
  :global(body) {
    font-family: system-ui, sans-serif;
    background: #0b1520;
    color: #e8eef4;
    margin: 0;
    padding: 1.5rem;
  }

  main {
    max-width: 640px;
    margin: 0 auto;
  }

  canvas {
    display: block;
    border-radius: 8px;
    background: #0b1520;
  }

  img {
    vertical-align: middle;
    margin-right: 0.5rem;
  }
</style>

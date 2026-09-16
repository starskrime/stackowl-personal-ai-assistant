// Story 1.5 (AD-20, AD-40): renders the StackOwl owl mark inside a
// `three/webgpu` `WebGPURenderer` scene, auto-falling back to WebGL2 when
// WebGPU isn't available (the default for headless Chromium on the build
// host -- see check.py's B5 step). The geometry comes from
// `logo/stackowl-mark.svg` at runtime via `SVGLoader`, matching how
// App.svelte's DOM `<img>` also references that same file (never
// hand-redrawn or duplicated as literal path data -- AD-40's own rule).
import * as THREE from "three/webgpu";
import { SVGLoader } from "three/examples/jsm/loaders/SVGLoader.js";
import { trustedTypesPolicy } from "./trusted-types-policy";

export type RendererBackend = "webgpu" | "webgl2";

// three's SVGLoader is not itself Trusted-Types-aware: it calls
// `DOMParser.parseFromString(text, 'image/svg+xml')` with a plain string,
// which `require-trusted-types-for 'script'` blocks outright (DOMParser's
// parseFromString is one of the sinks the directive gates). Patched through
// this kit's ONE shared Trusted Types policy (./trusted-types-policy) --
// never a second policy -- so SVGLoader keeps working under the enforced
// CSP without SVGLoader itself ever needing to change. Idempotent: safe if
// createOwlScene() is ever called more than once on the same page.
let domParserPatched = false;
function patchDomParserForTrustedTypes(): void {
  const policy = trustedTypesPolicy;
  if (domParserPatched || !policy) return;
  domParserPatched = true;
  const originalParseFromString = DOMParser.prototype.parseFromString;
  DOMParser.prototype.parseFromString = function patchedParseFromString(
    this: DOMParser,
    text: string,
    ...rest: Parameters<typeof originalParseFromString> extends [string, ...infer R] ? R : never
  ): Document {
    const trusted = policy.createHTML(text) as unknown as string;
    return originalParseFromString.call(this, trusted, ...rest);
  };
}

export interface OwlScene {
  /** Resolves once WebGPURenderer.init() has settled the WebGPU-vs-WebGL2
   * backend decision AND the owl mark geometry has been loaded and added to
   * the scene. */
  ready: Promise<RendererBackend>;
  /** Synchronous snapshot of the resolved backend -- null until `ready`
   * settles. Exposed on `window.__cspCheckRendererBackend` by App.svelte so
   * check.py's Playwright step can assert the fallback path without racing
   * the async `ready` promise itself. */
  getBackend(): RendererBackend | null;
  dispose(): void;
}

// A minimal structural type for the two backend classes THREE.WebGPURenderer
// can settle on -- `three`'s own public API only guarantees the renderer
// exposes SOME backend object, not a typed union of the two concrete
// classes, so this narrows just the two boolean flags this module reads.
interface RendererBackendFlags {
  isWebGPUBackend?: boolean;
  isWebGLBackend?: boolean;
}

export function createOwlScene(canvas: HTMLCanvasElement, svgUrl: string): OwlScene {
  const width = canvas.clientWidth || 320;
  const height = canvas.clientHeight || 240;

  const renderer = new THREE.WebGPURenderer({ canvas, antialias: true });
  renderer.setSize(width, height, false);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0b1520);

  const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100);
  camera.position.set(0, 0, 6);

  const group = new THREE.Group();
  scene.add(group);

  let resolvedBackend: RendererBackend | null = null;
  let animationHandle: number | null = null;
  let disposed = false;

  const ready: Promise<RendererBackend> = (async () => {
    // `init()` is what actually attempts to acquire a WebGPU adapter and,
    // on failure, synchronously swaps `renderer.backend` to the WebGL2
    // fallback constructed by the `getFallback` callback WebGPURenderer's
    // own constructor wires up -- see three's WebGPURenderer.js. Reading
    // `renderer.backend` before this resolves is a race; only after this
    // await is the flag below authoritative.
    await renderer.init();
    // `dispose()` may have run while `init()` was pending -- bail before
    // touching `renderer`/`group` any further rather than resolving a
    // WebGPU-vs-WebGL2 backend for, and building geometry into, a scene
    // whose renderer is already torn down.
    if (disposed) return resolvedBackend ?? "webgl2";
    const backend = renderer.backend as unknown as RendererBackendFlags;
    resolvedBackend = backend.isWebGPUBackend ? "webgpu" : "webgl2";

    patchDomParserForTrustedTypes();
    const loader = new SVGLoader();
    const data = await loader.loadAsync(svgUrl);
    // Same race, for the SVG fetch/parse await: `dispose()` may have run
    // while it was pending -- bail before adding mesh data to `group`.
    if (disposed) return resolvedBackend;
    const material = new THREE.MeshBasicMaterial({ color: 0xf4a340, side: THREE.DoubleSide, depthWrite: false });
    for (const path of data.paths) {
      for (const shape of path.toShapes()) {
        group.add(new THREE.Mesh(new THREE.ShapeGeometry(shape), material));
      }
    }
    // The source SVG's viewBox is 0..24 on both axes with +Y pointing DOWN
    // (SVG convention) -- flip Y and recentre so the mark reads upright and
    // centred at the origin in Three.js's own +Y-up, origin-centred
    // convention.
    group.scale.set(0.22, -0.22, 0.22);
    group.position.set(-2.6, 2.6, 0);

    const tick = (): void => {
      if (disposed) return;
      group.rotation.y += 0.008;
      renderer.render(scene, camera);
      animationHandle = requestAnimationFrame(tick);
    };
    tick();

    return resolvedBackend;
  })();

  return {
    ready,
    getBackend: () => resolvedBackend,
    dispose(): void {
      disposed = true;
      if (animationHandle !== null) {
        cancelAnimationFrame(animationHandle);
        animationHandle = null;
      }
      renderer.dispose();
    },
  };
}

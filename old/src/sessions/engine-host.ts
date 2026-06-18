import type { OwlEngine } from "../engine/runtime.js";

/** Factory + base context bundle passed to SessionRunner. */
export interface EngineHost {
  engineFactory: () => OwlEngine;
  baseContext: () => any;
}

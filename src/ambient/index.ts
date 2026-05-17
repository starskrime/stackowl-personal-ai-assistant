export type {
  SignalSource,
  SignalPriority,
  ContextSignal,
  SignalCollector,
  MeshState,
  AmbientRule,
  ConsentMap,
} from "./types.js";

export { DEFAULT_CONSENT } from "./types.js";

export {
  TimeContextCollector,
  SystemCollector,
  ActiveFileCollector,
  ClipboardCollector,
  FileSystemCollector,
} from "../signals/collectors.js";

export { SignalPool } from "../signals/pool.js";

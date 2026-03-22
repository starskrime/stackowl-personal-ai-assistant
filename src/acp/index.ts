/**
 * StackOwl — Agent Communication Protocol (ACP)
 *
 * Barrel exports for the ACP subsystem.
 */

export type {
  ACPMessage,
  ACPChannel,
  ACPCapability,
  SessionBridge,
  BridgePermissions,
  ACPStreamWriter,
  ACPStream,
  DeliveryStatus,
  ACPMessageHandler,
} from "./types.js";
export { ACPRouter } from "./router.js";
export { ACPBackpressure } from "./backpressure.js";
export { SessionBridgeFactory } from "./bridge.js";

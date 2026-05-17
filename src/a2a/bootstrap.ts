// src/a2a/bootstrap.ts
// Called once from src/index.ts AFTER the session store is initialized.
// Timing constraint: A2ARegistry must be populated before GatewayCore is instantiated.
// Initially empty — agents register here as the parliament system is built out.
import { A2ARegistry } from './registry.js';

export function registerDefaultAgents(_registry: A2ARegistry): void {
  // No default agents in v1. Parliament owl agents register themselves
  // during their own initialization via registry.register(new OwlAgent()).
}

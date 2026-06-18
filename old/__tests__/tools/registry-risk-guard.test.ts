import { describe, it, expect, vi } from 'vitest';
import { ToolRegistry } from '../../src/tools/registry.js';
import type { ToolRiskGuard } from '../../src/clarification/tool-risk-guard.js';

function makeRiskGuard(allowed: boolean): ToolRiskGuard {
  return {
    check: vi.fn().mockResolvedValue(
      allowed
        ? { allowed: true }
        : { allowed: false, confirmationId: 'cid1', userFacingMessage: 'Confirm deletion?' }
    ),
    resolveConfirmation: vi.fn(),
  } as any;
}

describe('ToolRegistry risk guard', () => {
  it('setRiskGuard() accepts a guard', () => {
    const registry = new ToolRegistry();
    expect(() => registry.setRiskGuard(makeRiskGuard(true))).not.toThrow();
  });

  it('proceeds normally when guard allows', async () => {
    const registry = new ToolRegistry();
    registry.setRiskGuard(makeRiskGuard(true));
    registry.register({
      definition: { name: 'TestTool', description: 'test', parameters: {} },
      execute: async () => 'done',
    });
    const result = await registry.execute('TestTool', {}, { cwd: '/tmp' });
    expect(result).toBe('done');
  });

  it('returns confirmation message when guard blocks', async () => {
    const registry = new ToolRegistry();
    registry.setRiskGuard(makeRiskGuard(false));
    registry.register({
      definition: { name: 'DangerTool', description: 'dangerous', parameters: {} },
      execute: async () => 'executed',
    });
    const result = await registry.execute('DangerTool', {}, { cwd: '/tmp' });
    expect(result).toContain('Confirm deletion?');
  });
});

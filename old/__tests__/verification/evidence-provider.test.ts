import { describe, it, expect, beforeEach } from 'vitest';
import { EvidenceProvider } from '../../src/verification/evidence-provider.js';

describe('EvidenceProvider', () => {
  let provider: EvidenceProvider;

  beforeEach(() => {
    provider = new EvidenceProvider({ maxArtifacts: 10 });
  });

  describe('recordToolResult', () => {
    it('should record tool result for task', () => {
      provider.recordToolResult('task-1', 'shell_command', 'output result');
      const evidence = provider.gatherEvidence('task-1');
      expect(evidence.artifacts.length).toBe(1);
      expect(evidence.artifacts[0].type).toBe('tool_result');
    });
  });

  describe('recordFileCreation', () => {
    it('should record file creation for task', () => {
      provider.recordFileCreation('task-1', '/path/to/file.txt', 'Created new file');
      const evidence = provider.gatherEvidence('task-1');
      expect(evidence.artifacts.length).toBe(1);
      expect(evidence.artifacts[0].type).toBe('file_creation');
      expect(evidence.artifacts[0].path).toBe('/path/to/file.txt');
    });
  });

  describe('recordFileModification', () => {
    it('should record file modification for task', () => {
      provider.recordFileModification('task-1', '/path/to/file.txt');
      const evidence = provider.gatherEvidence('task-1');
      expect(evidence.artifacts.length).toBe(1);
      expect(evidence.artifacts[0].type).toBe('file_modification');
    });
  });

  describe('recordCommandOutput', () => {
    it('should record command output for task', () => {
      provider.recordCommandOutput('task-1', 'ls -la', 'total 10\ndrwxr-xr-x staff   64 Apr 26 14:56 .');
      const evidence = provider.gatherEvidence('task-1');
      expect(evidence.artifacts.length).toBe(1);
      expect(evidence.artifacts[0].type).toBe('command_output');
      expect(evidence.artifacts[0].description).toContain('ls -la');
    });
  });

  describe('gatherEvidence', () => {
    it('should mark evidence as incomplete when no artifacts', () => {
      const evidence = provider.gatherEvidence('empty-task');
      expect(evidence.isComplete).toBe(false);
    });

    it('should mark evidence as complete when artifacts exist', () => {
      provider.recordToolResult('task-1', 'tool', 'result');
      const evidence = provider.gatherEvidence('task-1');
      expect(evidence.isComplete).toBe(true);
    });

    it('should limit artifacts to maxArtifacts setting', () => {
      for (let i = 0; i < 15; i++) {
        provider.recordToolResult('task-1', `tool_${i}`, `result_${i}`);
      }
      const evidence = provider.gatherEvidence('task-1');
      expect(evidence.artifacts.length).toBe(10);
    });
  });

  describe('formatEvidence', () => {
    it('should return "No evidence available" for empty evidence', () => {
      const evidence = provider.gatherEvidence('empty-task');
      const formatted = provider.formatEvidence(evidence);
      expect(formatted).toContain('No evidence available');
    });

    it('should format file creation evidence', () => {
      provider.recordFileCreation('task-1', '/path/to/file.txt');
      const evidence = provider.gatherEvidence('task-1');
      const formatted = provider.formatEvidence(evidence);
      expect(formatted).toContain('File Created');
      expect(formatted).toContain('/path/to/file.txt');
    });

    it('should format command output evidence', () => {
      provider.recordCommandOutput('task-1', 'echo hello', 'hello');
      const evidence = provider.gatherEvidence('task-1');
      const formatted = provider.formatEvidence(evidence);
      expect(formatted).toContain('Command Output');
      expect(formatted).toContain('echo hello');
    });

    it('should truncate long content', () => {
      const longOutput = 'x'.repeat(600);
      provider.recordCommandOutput('task-1', 'command', longOutput);
      const evidence = provider.gatherEvidence('task-1');
      const formatted = provider.formatEvidence(evidence);
      expect(formatted).toContain('...');
    });
  });

  describe('getEvidence', () => {
    it('should return undefined for unknown taskId', () => {
      expect(provider.getEvidence('unknown-task')).toBeUndefined();
    });

    it('should return cached evidence', () => {
      provider.recordToolResult('task-1', 'tool', 'result');
      provider.gatherEvidence('task-1');
      const cached = provider.getEvidence('task-1');
      expect(cached).toBeDefined();
      expect(cached?.artifacts.length).toBe(1);
    });
  });

  describe('clearEvidence', () => {
    it('should clear evidence for task', () => {
      provider.recordToolResult('task-1', 'tool', 'result');
      provider.gatherEvidence('task-1');
      provider.clearEvidence('task-1');
      const evidence = provider.gatherEvidence('task-1');
      expect(evidence.artifacts.length).toBe(0);
    });
  });
});
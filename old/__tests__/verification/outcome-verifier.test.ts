import { describe, it, expect, vi, beforeEach } from 'vitest';
import { OutcomeVerifier } from '../../src/verification/outcome-verifier.js';

describe('OutcomeVerifier', () => {
  let verifier: OutcomeVerifier;

  beforeEach(() => {
    verifier = new OutcomeVerifier({ confidenceThreshold: 0.7 });
  });

  describe('verifySync', () => {
    it('should return true for exact string match', () => {
      const result = verifier.verifySync('Hello world', 'Hello world');
      expect(result).toBe(true);
    });

    it('should return true for case-insensitive match', () => {
      const result = verifier.verifySync('HELLO WORLD', 'hello world');
      expect(result).toBe(true);
    });

    it('should return true when result contains intent', () => {
      const result = verifier.verifySync(
        'The result is Hello world and more text',
        'Hello world'
      );
      expect(result).toBe(true);
    });

    it('should return true when intent contains result', () => {
      const result = verifier.verifySync('Hello', 'Hello world');
      expect(result).toBe(true);
    });

    it('should return true for short intent word matching when all words match', () => {
      const result = verifier.verifySync('The answer is dog and cat', 'dog cat');
      expect(result).toBe(true);
    });

    it('should return false for unrelated strings', () => {
      const result = verifier.verifySync('The quick brown fox', 'hello world');
      expect(result).toBe(false);
    });
  });

  describe('getStatus / updateStatus', () => {
    it('should return undefined for unknown taskId', () => {
      const status = verifier.getStatus('unknown-task');
      expect(status).toBeUndefined();
    });

    it('should return updated status after verify', () => {
      verifier.updateStatus('task-1', 'passed');
      expect(verifier.getStatus('task-1')).toBe('passed');
    });

    it('should update status to failed', () => {
      verifier.updateStatus('task-2', 'failed');
      expect(verifier.getStatus('task-2')).toBe('failed');
    });
  });

  describe('getAllStatuses', () => {
    it('should return empty map initially', () => {
      const statuses = verifier.getAllStatuses();
      expect(statuses.size).toBe(0);
    });

    it('should return all tracked statuses', () => {
      verifier.updateStatus('task-1', 'passed');
      verifier.updateStatus('task-2', 'failed');
      verifier.updateStatus('task-3', 'pending');

      const statuses = verifier.getAllStatuses();
      expect(statuses.size).toBe(3);
      expect(statuses.get('task-1')).toBe('passed');
      expect(statuses.get('task-2')).toBe('failed');
      expect(statuses.get('task-3')).toBe('pending');
    });
  });

  describe('getVerification', () => {
    it('should return undefined for unknown taskId', () => {
      const verification = verifier.getVerification('unknown-task');
      expect(verification).toBeUndefined();
    });
  });

  describe('verify with no provider', () => {
    it('should return pending status when no provider available', async () => {
      const verification = await verifier.verify('task-1', 'result', 'intent');
      expect(verification.status).toBe('pending');
      expect(verification.confidence).toBe(0);
      expect(verifier.getStatus('task-1')).toBe('pending');
    });

    it('should use sync verification when syncStringMatch enabled', async () => {
      const verification = await verifier.verify(
        'task-2',
        'hello world',
        'hello world'
      );
      expect(verification.status).toBe('passed');
      expect(verification.confidence).toBe(1.0);
    });
  });
});
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { FalseDoneDetector } from '../../src/verification/false-done-detector.js';
import { OutcomeVerifier } from '../../src/verification/outcome-verifier.js';

describe('FalseDoneDetector', () => {
  let verifier: OutcomeVerifier;
  let detector: FalseDoneDetector;

  beforeEach(() => {
    verifier = new OutcomeVerifier({ confidenceThreshold: 0.7 });
    detector = new FalseDoneDetector(verifier, { confidenceThreshold: 0.7 });
  });

  describe('detect with short result', () => {
    it('should detect false DONE for result too short', async () => {
      const result = await detector.detect('task-1', 'hi', 'hello world');
      expect(result.isFalseDone).toBe(true);
      expect(result.reason).toContain('too short');
      expect(result.confidence).toBe(1.0);
    });
  });

  describe('detect with verification failure', () => {
    it('should detect false DONE when verification fails', async () => {
      const result = await detector.detect(
        'task-2',
        'This is a completely different result',
        'I want to build a website',
        undefined
      );
      expect(result.isFalseDone).toBe(true);
      expect(result.suggestedCorrection).toBeDefined();
    });
  });

  describe('shouldSelfCorrect', () => {
    it('should return false when no pending correction', () => {
      expect(detector.shouldSelfCorrect('unknown-task')).toBe(false);
    });

    it('should return true when false DONE detected', async () => {
      await detector.detect('task-1', 'hi', 'hello world');
      expect(detector.shouldSelfCorrect('task-1')).toBe(true);
    });
  });

  describe('getPendingCorrection', () => {
    it('should return undefined when no correction pending', () => {
      expect(detector.getPendingCorrection('unknown-task')).toBeUndefined();
    });

    it('should return correction after false DONE detection', async () => {
      await detector.detect('task-1', 'hi', 'hello world');
      const correction = detector.getPendingCorrection('task-1');
      expect(correction).toBeDefined();
      expect(correction?.isFalseDone).toBe(true);
    });
  });

  describe('clearPendingCorrection', () => {
    it('should clear pending correction', async () => {
      await detector.detect('task-1', 'hi', 'hello world');
      expect(detector.shouldSelfCorrect('task-1')).toBe(true);
      detector.clearPendingCorrection('task-1');
      expect(detector.shouldSelfCorrect('task-1')).toBe(false);
    });
  });

  describe('getDetectionHistory', () => {
    it('should return undefined for unknown taskId', () => {
      expect(detector.getDetectionHistory('unknown-task')).toBeUndefined();
    });

    it('should return detection result after detect', async () => {
      await detector.detect('task-1', 'hi', 'hello world');
      const history = detector.getDetectionHistory('task-1');
      expect(history).toBeDefined();
      expect(history?.isFalseDone).toBe(true);
    });
  });
});
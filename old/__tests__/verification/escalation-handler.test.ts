import { describe, it, expect, beforeEach } from 'vitest';
import {
  EscalationHandler,
  type ConfirmationClassifier,
} from '../../src/verification/escalation-handler.js';

const fakeClassifier =
  (intent: 'confirm' | 'reject' | 'unclear'): ConfirmationClassifier =>
  async () => ({ intent, confidence: 1, reasoning: 'fake' });

describe('EscalationHandler', () => {
  let handler: EscalationHandler;

  beforeEach(() => {
    handler = new EscalationHandler({ minConfidenceForEscalation: 0.5 });
  });

  describe('shouldEscalate', () => {
    it('should return true when confidence below threshold', () => {
      expect(handler.shouldEscalate(0.3)).toBe(true);
    });

    it('should return false when confidence above threshold', () => {
      expect(handler.shouldEscalate(0.7)).toBe(false);
    });

    it('should return false when confidence equals threshold', () => {
      expect(handler.shouldEscalate(0.5)).toBe(false);
    });
  });

  describe('createEscalationMessage', () => {
    it('should create escalation message for failed verification', () => {
      const escalation = handler.createEscalationMessage(
        'task-1',
        'Build a website',
        'Result text',
        {
          status: 'failed',
          confidence: 0.3,
          matchDetails: 'Result does not match intent',
          checkedAt: new Date().toISOString(),
        }
      );

      expect(escalation.message).toContain('verify');
      expect(escalation.context.taskId).toBe('task-1');
      expect(escalation.verificationResult.status).toBe('failed');
    });

    it('should create escalation message for low confidence verification', () => {
      const escalation = handler.createEscalationMessage(
        'task-2',
        'Write code',
        'Result text',
        {
          status: 'passed',
          confidence: 0.4,
          matchDetails: 'Low confidence match',
          checkedAt: new Date().toISOString(),
        }
      );

      expect(escalation.message).toContain('want to make sure');
    });
  });

  describe('handleUserResponse', () => {
    it('should record confirmation when classifier returns confirm', async () => {
      const h = new EscalationHandler({}, fakeClassifier('confirm'));
      h.createEscalationMessage(
        'task-1',
        'Build a website',
        'Result text',
        { status: 'failed', confidence: 0.3, checkedAt: new Date().toISOString() }
      );

      await h.handleUserResponse('task-1', 'Yes, that works great!');
      const record = h.getEscalationRecord('task-1');
      expect(record?.userConfirmed).toBe(true);
    });

    it('should record rejection when classifier returns reject', async () => {
      const h = new EscalationHandler({}, fakeClassifier('reject'));
      h.createEscalationMessage(
        'task-2',
        'Build a website',
        'Result text',
        { status: 'failed', confidence: 0.3, checkedAt: new Date().toISOString() }
      );

      await h.handleUserResponse('task-2', 'No, this is wrong');
      const record = h.getEscalationRecord('task-2');
      expect(record?.userConfirmed).toBe(false);
    });

    it('should treat unclear classifier verdict as non-confirmation', async () => {
      const h = new EscalationHandler({}, fakeClassifier('unclear'));
      h.createEscalationMessage(
        'task-3',
        'Build a website',
        'Result text',
        { status: 'failed', confidence: 0.3, checkedAt: new Date().toISOString() }
      );

      await h.handleUserResponse('task-3', 'Maybe later');
      const record = h.getEscalationRecord('task-3');
      expect(record?.userConfirmed).toBe(false);
    });

    it('should default to unclear when no classifier is wired', async () => {
      handler.createEscalationMessage(
        'task-4',
        'Build a website',
        'Result text',
        { status: 'failed', confidence: 0.3, checkedAt: new Date().toISOString() }
      );

      await handler.handleUserResponse('task-4', 'Yes');
      const record = handler.getEscalationRecord('task-4');
      expect(record?.userConfirmed).toBe(false);
    });
  });

  describe('getEscalationRecord', () => {
    it('should return undefined for unknown taskId', () => {
      expect(handler.getEscalationRecord('unknown-task')).toBeUndefined();
    });

    it('should return escalation record after creation', () => {
      handler.createEscalationMessage(
        'task-1',
        'Intent',
        'Result',
        { status: 'failed', confidence: 0.3, checkedAt: new Date().toISOString() }
      );

      const record = handler.getEscalationRecord('task-1');
      expect(record).toBeDefined();
      expect(record?.taskId).toBe('task-1');
    });
  });

  describe('getPendingEscalation', () => {
    it('should return pending escalation', () => {
      handler.createEscalationMessage(
        'task-1',
        'Intent',
        'Result',
        { status: 'failed', confidence: 0.3, checkedAt: new Date().toISOString() }
      );

      const pending = handler.getPendingEscalation('task-1');
      expect(pending).toBeDefined();
    });

    it('should return undefined after user responds', async () => {
      const h = new EscalationHandler({}, fakeClassifier('confirm'));
      h.createEscalationMessage(
        'task-1',
        'Intent',
        'Result',
        { status: 'failed', confidence: 0.3, checkedAt: new Date().toISOString() }
      );

      await h.handleUserResponse('task-1', 'Yes');
      const pending = h.getPendingEscalation('task-1');
      expect(pending).toBeUndefined();
    });
  });

  describe('triggerEscalation', () => {
    it('should return escalation message when pending', () => {
      handler.createEscalationMessage(
        'task-1',
        'Intent',
        'Result',
        { status: 'failed', confidence: 0.3, checkedAt: new Date().toISOString() }
      );

      const escalation = handler.triggerEscalation('task-1');
      expect(escalation).toBeDefined();
      expect(escalation?.context.taskId).toBe('task-1');
    });
  });

  describe('getAllEscalations', () => {
    it('should return empty map initially', () => {
      const all = handler.getAllEscalations();
      expect(all.size).toBe(0);
    });

    it('should return all escalations', () => {
      handler.createEscalationMessage(
        'task-1',
        'Intent1',
        'Result1',
        { status: 'failed', confidence: 0.3, checkedAt: new Date().toISOString() }
      );
      handler.createEscalationMessage(
        'task-2',
        'Intent2',
        'Result2',
        { status: 'failed', confidence: 0.3, checkedAt: new Date().toISOString() }
      );

      const all = handler.getAllEscalations();
      expect(all.size).toBe(2);
    });
  });
});

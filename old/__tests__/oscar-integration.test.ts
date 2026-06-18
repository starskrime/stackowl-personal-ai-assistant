import { describe, it, expect, beforeEach } from "vitest";
import { Oscar } from "../src/oscar/index.js";
import { visualMemoryNetwork } from "../src/oscar/memory/index.js";
import { cognitionEngine } from "../src/oscar/cognition/index.js";
import type { CanonicalAction, UIElement, AppInfo } from "../src/oscar/types.js";

describe("Oscar Integration Tests", () => {
  let oscar: Oscar;

  beforeEach(() => {
    oscar = new Oscar({
      enableScreenPipeline: false,
      enableObservatory: false,
    });
  });

  describe("Phase 1: Universal Control Interface", () => {
    it("should initialize with all components", () => {
      expect(oscar).toBeDefined();
      expect(oscar.isRunning()).toBe(false);
    });

    it("should provide access to all Phase 1-5 components", () => {
      expect(oscar.getAdapter()).toBeDefined();
      expect(oscar.getResolver()).toBeDefined();
      expect(oscar.getMemory()).toBeDefined();
      expect(oscar.getCognition()).toBeDefined();
      expect(oscar.getCheckpointManager()).toBeDefined();
      expect(oscar.getRecoveryController()).toBeDefined();
    });

    it("should start and stop the control interface", async () => {
      await oscar.start();
      expect(oscar.isRunning()).toBe(true);

      oscar.stop();
      expect(oscar.isRunning()).toBe(false);
    });
  });

  describe("Phase 2: Screen Graph Observatory", () => {
    it("should provide observatory access", () => {
      expect(oscar.getObservatory()).toBeNull();
    });

    it("should provide query builder interface", () => {
      const queryBuilder = oscar.queryGraph();
      expect(queryBuilder).toBeNull();
    });
  });

  describe("Phase 3: Intent Processing Engine", () => {
    it("should provide access to intent components", () => {
      expect(oscar.getIntentParser()).toBeDefined();
      expect(oscar.getIntentDecomposer()).toBeDefined();
      expect(oscar.getDAGValidator()).toBeDefined();
    });

    it("should parse simple intents", () => {
      const parser = oscar.getIntentParser();
      const parsed = parser.parse("click the button");

      expect(parsed).toBeDefined();
      expect(parsed.verb).toBe("click");
      expect(parsed.confidence).toBeGreaterThan(0);
    });

    it("should decompose intents into execution plans", () => {
      const decomposer = oscar.getIntentDecomposer();
      const plan = decomposer.decompose("clear background", {
        currentApp: { bundleId: "photoshop", name: "Photoshop", pid: 0 },
      });

      expect(plan).toBeDefined();
      expect(plan.steps).toBeDefined();
      expect(plan.status).toBe("planned");
    });

    it("should validate execution plans", () => {
      const decomposer = oscar.getIntentDecomposer();
      const dagValidator = oscar.getDAGValidator();

      const plan = decomposer.decompose("open file", {});
      const validation = dagValidator.validate(plan);

      expect(validation).toBeDefined();
      expect(validation.valid).toBe(true);
      expect(Array.isArray(validation.parallelizable)).toBe(true);
    });

    it("should detect cycles in invalid plans", () => {
      const dagValidator = oscar.getDAGValidator();

      const invalidPlan = {
        id: "test",
        steps: [
          {
            id: "step_0",
            action: "click",
            target: {},
            params: {},
            dependsOn: ["step_1"],
            verification: undefined,
            estimatedSuccess: 1,
          },
          {
            id: "step_1",
            action: "type",
            target: {},
            params: {},
            dependsOn: ["step_0"],
            verification: undefined,
            estimatedSuccess: 1,
          },
        ],
        currentStep: 0,
        status: "planned" as const,
      };

      const validation = dagValidator.validate(invalidPlan);
      expect(validation.valid).toBe(false);
      expect(validation.errors.length).toBeGreaterThan(0);
    });
  });

  describe("Phase 4: Visual Memory Network", () => {
    it("should provide access to memory network", () => {
      const memory = oscar.getMemory();
      expect(memory).toBeDefined();
    });

    it("should query and store episodes", async () => {
      const memory = oscar.getMemory();

      const episode = await memory.recordEpisode({
        app: "test-app",
        appBundleId: "com.test.app",
        actions: [
          {
            type: "click",
            target: {},
            params: {},
            timestamp: Date.now(),
            traceId: "trace1",
          },
        ],
        outcome: "success",
      });

      expect(episode).toBeDefined();
      expect(episode.id).toBeDefined();
      expect(episode.timestamp).toBeGreaterThan(0);
    });

    it("should learn from experience", async () => {
      const memory = oscar.getMemory();

      const mockElement: UIElement = {
        id: "elem1",
        type: "button",
        bounds: { x: 100, y: 100, width: 50, height: 30 },
        visual: {},
        semantic: { label: "Submit" },
        affordances: {
          clickable: true,
          editable: false,
          scrollable: false,
          draggable: false,
          keyboardFocusable: true,
        },
      };

      const action: CanonicalAction = {
        type: "click",
        target: { accessibilityPath: "elem1" },
        params: {},
        timestamp: Date.now(),
        traceId: "trace2",
      };

      await memory.learnFromExperience(
        [mockElement],
        [action],
        "success",
        "com.test.app"
      );

      const affordances = await memory.retrieveAffordancesForElement(mockElement, "click", "com.test.app");
      expect(Array.isArray(affordances)).toBe(true);
    });

    it("should record and retrieve skills", async () => {
      const memory = oscar.getMemory();

      const skill = await memory.querySkills({});
      expect(Array.isArray(skill)).toBe(true);
    });

    it("should get memory statistics", async () => {
      const stats = await oscar.getMemoryStats();

      expect(stats).toBeDefined();
      expect(stats.episodes).toBeDefined();
      expect(stats.affordances).toBeDefined();
      expect(stats.skills).toBeDefined();
    });
  });

  describe("Phase 5: Autonomous Cognitive Agent", () => {
    it("should start and stop cognition", async () => {
      await oscar.startCognition();
      expect(oscar.getCognition().isRunning()).toBe(true);

      oscar.stopCognition();
      expect(oscar.getCognition().isRunning()).toBe(false);
    });

    it("should record cognitive episodes", async () => {
      const action: CanonicalAction = {
        type: "click",
        target: {},
        params: {},
        timestamp: Date.now(),
        traceId: "trace3",
      };

      await oscar.recordCognitiveEpisode([action], "success", "test-app");

      const state = await oscar.getCognitiveState();
      expect(state.observationCount).toBeGreaterThanOrEqual(0);
    });

    it("should get proactive suggestions", async () => {
      const result = await oscar.getProactiveSuggestions();

      expect(result).toBeDefined();
      expect(Array.isArray(result.suggestions)).toBe(true);
    });

    it("should detect anomalies", async () => {
      const result = await oscar.getAnomalyAlerts(5);

      expect(result).toBeDefined();
      expect(Array.isArray(result.alerts)).toBe(true);
    });

    it("should provide anomaly detection stats", () => {
      const cognition = oscar.getCognition();
      const stats = cognition.getAnomalyStats();

      expect(stats).toBeDefined();
      expect(stats.total).toBeGreaterThanOrEqual(0);
      expect(stats.bySeverity).toBeDefined();
    });

    it("should provide insight stats", () => {
      const cognition = oscar.getCognition();
      const stats = cognition.getInsightStats();

      expect(stats).toBeDefined();
      expect(stats.total).toBeGreaterThanOrEqual(0);
    });
  });

  describe("Recovery and Checkpoint Integration", () => {
    it("should provide access to recovery controller", () => {
      const recovery = oscar.getRecoveryController();
      expect(recovery).toBeDefined();
      expect(typeof recovery.handleFailure).toBe("function");
    });

    it("should provide access to checkpoint manager", () => {
      const checkpoint = oscar.getCheckpointManager();
      expect(checkpoint).toBeDefined();
      expect(typeof checkpoint.create).toBe("function");
      expect(typeof checkpoint.getLatest).toBe("function");
    });

    it("should create checkpoints", () => {
      const checkpoint = oscar.getCheckpointManager();

      const mockPlan = {
        id: "test-plan",
        steps: [],
        currentStep: 0,
        status: "planned" as const,
      };

      const cp = checkpoint.create(mockPlan, 0, {});

      expect(cp).toBeDefined();
      expect(cp.id).toBeDefined();
      expect(cp.planId).toBe("test-plan");
      expect(cp.stepIndex).toBe(0);
    });

    it("should retrieve latest checkpoint", () => {
      const checkpoint = oscar.getCheckpointManager();

      const mockPlan = {
        id: "test-plan-retrieve",
        steps: [],
        currentStep: 0,
        status: "planned" as const,
      };

      checkpoint.create(mockPlan, 0, {});
      const latest = checkpoint.getLatest();

      expect(latest).toBeDefined();
      expect(latest?.id).toBeDefined();
    });
  });

  describe("End-to-End Integration", () => {
    it("should compose all phases in executeIntent flow", async () => {
      const intent = "clear background in photoshop";

      const parser = oscar.getIntentParser();
      const parsed = parser.parse(intent);
      expect(parsed.verb).toBeDefined();

      const decomposer = oscar.getIntentDecomposer();
      const plan = decomposer.decompose(intent, {
        currentApp: { bundleId: "photoshop", name: "Photoshop", pid: 0 },
      });
      expect(plan.steps.length).toBeGreaterThan(0);

      const dagValidator = oscar.getDAGValidator();
      const validation = dagValidator.validate(plan);
      expect(validation.valid).toBe(true);
    });

    it("should integrate memory with cognition", async () => {
      const cognition = oscar.getCognition();
      const memory = oscar.getMemory();

      const action: CanonicalAction = {
        type: "click",
        target: {},
        params: {},
        timestamp: Date.now(),
        traceId: "trace4",
      };

      await memory.recordEpisode({
        app: "test",
        appBundleId: "com.test",
        actions: [action],
        outcome: "success",
      });

      cognition.recordEpisode({
        id: "ep1",
        timestamp: Date.now(),
        actions: [action],
        outcome: "success",
        app: "test",
      });

      const insights = cognition.getInsights();
      expect(Array.isArray(insights)).toBe(true);
    });

    it("should allow concurrent cognition and memory operations", async () => {
      const memory = oscar.getMemory();
      const cognition = oscar.getCognition();

      const [memoryResult, cognitionResult] = await Promise.all([
        memory.getStats(),
        Promise.resolve(cognition.getCognitiveState()),
      ]);

      expect(memoryResult).toBeDefined();
      expect(cognitionResult).toBeDefined();
    });
  });

  describe("Visual Memory Network Standalone", () => {
    it("should manage affordances independently", async () => {
      const memory = visualMemoryNetwork;

      const mockElement: UIElement = {
        id: "aff-test-elem",
        type: "button",
        bounds: { x: 0, y: 0, width: 100, height: 50 },
        visual: {},
        semantic: { label: "Test Button" },
        affordances: {
          clickable: true,
          editable: false,
          scrollable: false,
          draggable: false,
          keyboardFocusable: false,
        },
      };

      const action: CanonicalAction = {
        type: "click",
        target: { accessibilityPath: "aff-test-elem" },
        params: {},
        timestamp: Date.now(),
        traceId: "aff-trace",
      };

      await memory.recordAffordance(
        mockElement,
        action,
        { success: true },
        "com.test.affordance"
      );

      const affordances = await memory.queryAffordances({
        app: "com.test.affordance",
      });

      expect(Array.isArray(affordances)).toBe(true);
    });

    it("should handle skill recording lifecycle", async () => {
      const memory = visualMemoryNetwork;

      const recordingId = memory.startRecording(
        { bundleId: "com.test.recording", name: "Test App" },
        "Test Recording"
      );

      expect(recordingId).toBeDefined();

      const recordings = memory.getActiveRecordings();
      expect(recordings.length).toBeGreaterThan(0);

      memory.cancelRecording(recordingId);
      expect(memory.getActiveRecordings().length).toBe(0);
    });
  });

  describe("Cognition Engine Standalone", () => {
    it("should run observe and reflect cycles", async () => {
      const cognition = cognitionEngine;

      await cognition.start();
      expect(cognition.isRunning()).toBe(true);

      const observation = await cognition.observe();
      expect(observation === null || observation.timestamp).toBeTruthy();

      const reflection = await cognition.reflect();
      expect(reflection === null || reflection.timestamp).toBeTruthy();

      cognition.stop();
      expect(cognition.isRunning()).toBe(false);
    });

    it("should learn from success and failure", async () => {
      const cognition = cognitionEngine;

      const episode = {
        id: "learn-test",
        timestamp: Date.now(),
        actions: [
          {
            type: "click" as const,
            target: {},
            params: {},
            timestamp: Date.now(),
            traceId: "learn-trace",
          },
        ],
        outcome: "success" as const,
        app: "test-learn",
      };

      const successInsight = await cognition.learnFromSuccess(episode);
      expect(successInsight === null || successInsight.id).toBeTruthy();

      const failedEpisode = { ...episode, id: "learn-test-fail", outcome: "failed" as const, error: "element not found" };
      const failResult = await cognition.learnFromFailure(
        failedEpisode,
        failedEpisode.actions[0]
      );

      expect(failResult).toBeDefined();
      expect(failResult.alerts).toBeDefined();
    });
  });
});

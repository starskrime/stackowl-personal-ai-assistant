import type { ToolImplementation, ToolContext } from "../tools/registry.js";
import { oscar } from "./index.js";
import type { CanonicalTarget, VerificationCondition } from "./types.js";
import type { ToolDefinition } from "../providers/base.js";

export interface ComputerToolConfig {
  name?: string;
  description?: string;
  requireVerification?: boolean;
  defaultTimeout?: number;
}

export class ComputerTool implements ToolImplementation {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  definition: ToolDefinition;

  private requireVerification: boolean;
  private defaultTimeout: number;

  constructor(config: ComputerToolConfig = {}) {
    this.name = config.name || "computer";
    this.description =
      config.description ||
      "Control the computer to perform actions like clicking, typing, launching apps, and more. Use this when the user asks to interact with applications, files, or the operating system.";
    
    this.definition = {
      name: this.name,
      description: this.description,
      parameters: {
        type: "object",
        properties: {
          action: {
            type: "string",
            description: "The action to perform: click, type, hotkey, launch, close, scroll, drag, observe",
          },
          target: {
            type: "object",
            description: "Target specification for the action",
          },
          params: {
            type: "object",
            description: "Additional parameters for the action",
          },
        },
        required: ["action"],
      },
    };
    
    this.parameters = {
      action: {
        type: "string",
        description: "The action to perform: click, type, hotkey, launch, close, scroll, drag, observe",
        required: true,
        enum: ["click", "type", "hotkey", "launch", "close", "scroll", "drag", "observe"],
      },
      target: {
        type: "object",
        description: "Target specification for the action",
        required: false,
        properties: {
          label: { type: "string", description: "Element label to find" },
          role: { type: "string", description: "Element role (button, textfield, etc.)" },
          x: { type: "number", description: "X coordinate (for visual/coordinates targeting)" },
          y: { type: "number", description: "Y coordinate (for visual/coordinates targeting)" },
          width: { type: "number", description: "Width (for region targeting)" },
          height: { type: "number", description: "Height (for region targeting)" },
        },
      },
      params: {
        type: "object",
        description: "Additional parameters for the action",
        required: false,
        properties: {
          text: { type: "string", description: "Text to type (for type action)" },
          key: { type: "string", description: "Key for hotkey action" },
          modifiers: {
            type: "array",
            items: { type: "string" },
            description: "Modifier keys (command, control, option, shift)",
          },
          button: {
            type: "string",
            description: "Mouse button (left, right, middle)",
            enum: ["left", "right", "middle"],
          },
          clickCount: { type: "number", description: "Number of clicks" },
          direction: {
            type: "string",
            description: "Scroll direction",
            enum: ["up", "down", "left", "right"],
          },
          amount: { type: "number", description: "Scroll amount" },
          application: { type: "string", description: "App name to launch" },
        },
      },
      verification: {
        type: "object",
        description: "Verification condition",
        required: false,
        properties: {
          type: {
            type: "string",
            enum: ["element_exists", "element_focused", "window_opened", "window_closed", "text_appeared"],
          },
          label: { type: "string", description: "Expected element label" },
          role: { type: "string", description: "Expected element role" },
          timeout: { type: "number", description: "Timeout in ms" },
        },
      },
    };
    this.requireVerification = config.requireVerification ?? false;
    this.defaultTimeout = config.defaultTimeout ?? 2000;
  }

  async execute(params: Record<string, unknown>, _context?: ToolContext): Promise<string> {
    try {
      const action = params.action as string;
      const target = this.parseTarget(params.target as Record<string, unknown> | undefined);
      const actionParams = this.parseParams(params.params as Record<string, unknown> | undefined);

      if (!oscar.isRunning()) {
        await oscar.start();
      }

      switch (action) {
        case "observe": {
          const observation = await oscar.observe();
          return `Observed screen: ${observation.focusedApp || "no app"} focused. ` +
            `Windows: ${observation.accessibilityState.windows.length}, ` +
            `Elements: ${observation.accessibilityState.elements.size}, ` +
            `Screen: ${observation.screenBuffer.width}x${observation.screenBuffer.height}`;
        }

        case "click": {
          const verification = this.buildVerification(
            actionParams.verification as Record<string, unknown> | undefined
          );
          const result = await oscar.click(target, actionParams, verification);
          if (result.success) {
            return `Click successful (${result.attempts} attempt${result.attempts === 1 ? "" : "s"})`;
          }
          return `Click failed: ${result.error}`;
        }

        case "type": {
          const text = actionParams.text as string;
          if (!text) {
            return "Type failed: No text provided";
          }
          const verification = this.buildVerification(
            actionParams.verification as Record<string, unknown> | undefined
          );
          const result = await oscar.type(text, target, verification);
          if (result.success) {
            return `Typed "${text.substring(0, 50)}${text.length > 50 ? "..." : ""}" (${result.attempts} attempt${result.attempts === 1 ? "" : "s"})`;
          }
          return `Type failed: ${result.error}`;
        }

        case "hotkey": {
          const key = actionParams.key as string;
          const modifiers = (actionParams.modifiers as string[]) || [];
          if (!key) {
            return "Hotkey failed: No key provided";
          }
          const result = await oscar.hotkey(key, modifiers);
          if (result.success) {
            const combo = [...modifiers, key].join("+");
            return `Hotkey ${combo} successful`;
          }
          return `Hotkey failed: ${result.error}`;
        }

        case "launch": {
          const app = actionParams.application as string;
          if (!app) {
            return "Launch failed: No application specified";
          }
          const result = await oscar.launch(app);
          if (result.success) {
            return `Launched ${app}`;
          }
          return `Launch failed: ${result.error}`;
        }

        case "close": {
          const result = await oscar.close();
          if (result.success) {
            return "Closed current window";
          }
          return `Close failed: ${result.error}`;
        }

        case "scroll": {
          const direction = (actionParams.direction as "up" | "down" | "left" | "right") || "down";
          const amount = actionParams.amount as number;
          const result = await oscar.scroll(direction, amount);
          if (result.success) {
            return `Scrolled ${direction}`;
          }
          return `Scroll failed: ${result.error}`;
        }

        case "drag": {
          const fromX = actionParams.fromX as number;
          const fromY = actionParams.fromY as number;
          const toX = actionParams.toX as number;
          const toY = actionParams.toY as number;
          if (fromX === undefined || fromY === undefined || toX === undefined || toY === undefined) {
            return "Drag failed: Missing coordinates (fromX, fromY, toX, toY required)";
          }
          const result = await oscar.drag(fromX, fromY, toX, toY);
          if (result.success) {
            return `Dragged from (${fromX}, ${fromY}) to (${toX}, ${toY})`;
          }
          return `Drag failed: ${result.error}`;
        }

        default:
          return `Unknown action: ${action}`;
      }
    } catch (error) {
      return `Computer tool error: ${String(error)}`;
    }
  }

  private parseTarget(target: Record<string, unknown> | undefined): CanonicalTarget {
    if (!target) return {};

    const canonical: CanonicalTarget = {};

    if (target.label || target.role) {
      canonical.semanticSelector = {
        label: target.label as string | undefined,
        role: target.role as string | undefined,
      };
    }

    if (typeof target.x === "number" && typeof target.y === "number") {
      if (typeof target.width === "number" && typeof target.height === "number") {
        canonical.visualRegion = {
          x: target.x,
          y: target.y,
          width: target.width,
          height: target.height,
        };
      } else {
        canonical.visualRegion = {
          x: target.x,
          y: target.y,
          width: 1,
          height: 1,
        };
      }
    }

    return canonical;
  }

  private parseParams(params: Record<string, unknown> | undefined): Record<string, unknown> {
    return params || {};
  }

  private buildVerification(
    verification: Record<string, unknown> | undefined
  ): VerificationCondition | undefined {
    if (!verification && !this.requireVerification) return undefined;

    if (verification) {
      return {
        type: verification.type as VerificationCondition["type"],
        expected: verification.expected as Record<string, unknown> | undefined,
        timeout: verification.timeout as number | undefined,
      };
    }

    return {
      type: "element_focused",
      timeout: this.defaultTimeout,
    };
  }
}

export const computerTool = new ComputerTool();

export function registerComputerTool(): void {
  const { toolRegistry } = require("../tools/registry.js");
  toolRegistry.register(computerTool);
  console.log("[Oscar] Computer tool registered with StackOwl");
}

export default ComputerTool;

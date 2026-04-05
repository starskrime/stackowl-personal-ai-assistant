import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ProviderRegistry } from "../src/providers/registry.js";
import type {
  ModelProvider,
  ChatMessage,
  ChatResponse,
  ChatOptions,
  ToolDefinition,
  StreamChunk,
  StreamEvent,
  EmbeddingResponse,
  ProviderConfig,
  ToolCall,
} from "../src/providers/base.js";

vi.mock("../src/logger.js", () => ({
  log: {
    engine: {
      info: vi.fn(),
      warn: vi.fn(),
      debug: vi.fn(),
      error: vi.fn(),
    },
  },
}));

function makeMockProvider(
  overrides: Partial<ModelProvider> = {},
): ModelProvider {
  const mockChat = vi.fn().mockResolvedValue({
    content: "mock response",
    model: "mock-model",
    finishReason: "stop",
    usage: { promptTokens: 10, completionTokens: 20, totalTokens: 30 },
  });
  const mockChatWithTools = vi.fn().mockResolvedValue({
    content: "mock response",
    model: "mock-model",
    finishReason: "stop",
    usage: { promptTokens: 10, completionTokens: 20, totalTokens: 30 },
  });
  const mockChatStream = vi.fn().mockResolvedValue({
    async *[Symbol.iterator]() {
      yield { content: "chunk", done: true };
    },
  });
  const mockEmbed = vi.fn().mockResolvedValue({
    embedding: [0.1, 0.2, 0.3],
    model: "mock-embed-model",
  });
  const mockListModels = vi.fn().mockResolvedValue(["mock-model"]);
  const mockHealthCheck = vi.fn().mockResolvedValue(true);

  return {
    name: "mock",
    chat: mockChat,
    chatWithTools: mockChatWithTools,
    chatStream: mockChatStream,
    embed: mockEmbed,
    listModels: mockListModels,
    healthCheck: mockHealthCheck,
    ...overrides,
  } as unknown as ModelProvider;
}

const mockMessages: ChatMessage[] = [{ role: "user", content: "Hello" }];

const mockTools: ToolDefinition[] = [
  {
    name: "get_weather",
    description: "Get weather for a location",
    parameters: {
      type: "object",
      properties: {
        location: { type: "string", description: "City name" },
      },
      required: ["location"],
    },
  },
];

describe("ProviderRegistry", () => {
  let registry: ProviderRegistry;

  beforeEach(() => {
    registry = new ProviderRegistry();
  });

  describe("register", () => {
    it("registers a provider with a built-in factory name", () => {
      registry.register({ name: "ollama", baseUrl: "http://localhost:11434" });
      expect(registry.listProviders()).toContain("ollama");
    });

    it("registers multiple providers", () => {
      registry.register({ name: "ollama", baseUrl: "http://localhost:11434" });
      registry.register({ name: "anthropic", apiKey: "sk-ant-test" });
      const providers = registry.listProviders();
      expect(providers).toContain("ollama");
      expect(providers).toContain("anthropic");
    });

    it("throws for unknown provider without baseUrl", () => {
      expect(() => registry.register({ name: "unknown-provider" })).toThrow(
        /Unknown provider/,
      );
    });

    it("auto-detects provider from baseUrl for openrouter", () => {
      registry.register({
        name: "my-openrouter",
        baseUrl: "https://openrouter.ai/api/v1",
        apiKey: "test-key",
      });
      expect(registry.listProviders()).toContain("my-openrouter");
    });

    it("auto-detects provider from baseUrl for together", () => {
      registry.register({
        name: "my-together",
        baseUrl: "https://api.together.xyz/v1",
        apiKey: "test-key",
      });
      expect(registry.listProviders()).toContain("my-together");
    });

    it("auto-detects provider from baseUrl for groq", () => {
      registry.register({
        name: "my-groq",
        baseUrl: "https://api.groq.com/openai/v1",
        apiKey: "test-key",
      });
      expect(registry.listProviders()).toContain("my-groq");
    });

    it("auto-detects provider from baseUrl for deepseek", () => {
      registry.register({
        name: "my-deepseek",
        baseUrl: "https://api.deepseek.com/v1",
        apiKey: "test-key",
      });
      expect(registry.listProviders()).toContain("my-deepseek");
    });

    it("auto-detects provider from baseUrl for lmstudio", () => {
      registry.register({
        name: "my-lmstudio",
        baseUrl: "http://127.0.0.1:1234/v1",
      });
      expect(registry.listProviders()).toContain("my-lmstudio");
    });

    it("auto-detects provider from apiKey for anthropic", () => {
      registry.register({
        name: "my-anthropic",
        apiKey: "sk-ant-api11-test",
      });
      expect(registry.listProviders()).toContain("my-anthropic");
    });

    it("auto-detects from baseUrl containing :11434 as ollama", () => {
      registry.register({
        name: "local-ollama",
        baseUrl: "http://localhost:11434",
      });
      expect(registry.listProviders()).toContain("local-ollama");
    });

    it("auto-detects from baseUrl containing ollama string", () => {
      registry.register({
        name: "remote-ollama",
        baseUrl: "https://ollama.example.com/api",
      });
      expect(registry.listProviders()).toContain("remote-ollama");
    });

    it("uses openai-compatible as fallback for unknown baseUrl", () => {
      registry.register({
        name: "custom-provider",
        baseUrl: "https://custom.api.example.com/v1",
        apiKey: "test-key",
      });
      expect(registry.listProviders()).toContain("custom-provider");
    });

    it("registers minimax provider", () => {
      registry.register({
        name: "minimax",
        baseUrl: "https://api.minimax.io/anthropic",
        apiKey: "test-key",
      });
      expect(registry.listProviders()).toContain("minimax");
    });

    it("registers openai-compatible provider", () => {
      registry.register({ name: "openai-compatible", apiKey: "test-key" });
      expect(registry.listProviders()).toContain("openai-compatible");
    });

    it("registers openai provider", () => {
      registry.register({ name: "openai", apiKey: "test-key" });
      expect(registry.listProviders()).toContain("openai");
    });

    it("registers together provider", () => {
      registry.register({
        name: "together",
        apiKey: "test-key",
      });
      expect(registry.listProviders()).toContain("together");
    });

    it("registers groq provider", () => {
      registry.register({
        name: "groq",
        apiKey: "test-key",
      });
      expect(registry.listProviders()).toContain("groq");
    });

    it("registers deepseek provider", () => {
      registry.register({
        name: "deepseek",
        apiKey: "test-key",
      });
      expect(registry.listProviders()).toContain("deepseek");
    });

    it("registers lmstudio provider", () => {
      registry.register({
        name: "lmstudio",
        apiKey: "test-key",
      });
      expect(registry.listProviders()).toContain("lmstudio");
    });
  });

  describe("setDefault", () => {
    it("sets the default provider", () => {
      registry.register({ name: "ollama", baseUrl: "http://localhost:11434" });
      registry.register({ name: "anthropic", apiKey: "sk-ant-test" });
      registry.setDefault("ollama");
      expect(registry.getDefault()).toBeDefined();
    });

    it("throws if provider name not registered", () => {
      expect(() => registry.setDefault("nonexistent")).toThrow(
        /Cannot set default/,
      );
    });
  });

  describe("get", () => {
    it("retrieves a registered provider by name", () => {
      registry.register({ name: "ollama", baseUrl: "http://localhost:11434" });
      const provider = registry.get("ollama");
      expect(provider).toBeDefined();
      expect(provider.name).toBe("ollama");
    });

    it("retrieves the default provider when name is omitted", () => {
      registry.register({ name: "ollama", baseUrl: "http://localhost:11434" });
      registry.register({ name: "anthropic", apiKey: "sk-ant-test" });
      registry.setDefault("anthropic");
      const provider = registry.get();
      expect(provider.name).toBe("anthropic");
    });

    it("throws when no name provided and no default set", () => {
      expect(() => registry.get()).toThrow(/No provider specified/);
    });

    it("throws for unknown provider name", () => {
      registry.register({ name: "ollama", baseUrl: "http://localhost:11434" });
      expect(() => registry.get("unknown")).toThrow(/not found/);
    });
  });

  describe("getDefault", () => {
    it("returns the default provider", () => {
      registry.register({ name: "ollama", baseUrl: "http://localhost:11434" });
      registry.setDefault("ollama");
      expect(registry.getDefault().name).toBe("ollama");
    });

    it("throws when no default set", () => {
      expect(() => registry.getDefault()).toThrow(/No provider specified/);
    });
  });

  describe("listProviders", () => {
    it("returns empty array when no providers registered", () => {
      expect(registry.listProviders()).toEqual([]);
    });

    it("returns all registered provider names", () => {
      registry.register({ name: "ollama", baseUrl: "http://localhost:11434" });
      registry.register({ name: "anthropic", apiKey: "sk-ant-test" });
      const providers = registry.listProviders();
      expect(providers).toHaveLength(2);
      expect(providers).toContain("ollama");
      expect(providers).toContain("anthropic");
    });
  });

  describe("healthCheckAll", () => {
    it("runs health checks on all registered providers", async () => {
      const mockOllama = makeMockProvider({
        name: "ollama",
        healthCheck: vi.fn().mockResolvedValue(true),
      });
      const mockAnthropic = makeMockProvider({
        name: "anthropic",
        healthCheck: vi.fn().mockResolvedValue(false),
      });

      registry.register({ name: "ollama", baseUrl: "http://localhost:11434" });
      registry.register({ name: "anthropic", apiKey: "sk-ant-test" });

      const results = await registry.healthCheckAll();
      expect(results).toHaveProperty("ollama");
      expect(results).toHaveProperty("anthropic");
    });
  });
});

describe("Base Types", () => {
  describe("ChatMessage", () => {
    it("accepts valid message roles", () => {
      const roles: Array<"system" | "user" | "assistant" | "tool"> = [
        "system",
        "user",
        "assistant",
        "tool",
      ];
      for (const role of roles) {
        const msg: ChatMessage = { role, content: "test" };
        expect(msg.role).toBe(role);
      }
    });

    it("accepts message with tool calls", () => {
      const msg: ChatMessage = {
        role: "assistant",
        content: "I'll check the weather",
        toolCalls: [
          { id: "tc_1", name: "get_weather", arguments: { location: "NYC" } },
        ],
      };
      expect(msg.toolCalls).toHaveLength(1);
      expect(msg.toolCalls![0].name).toBe("get_weather");
    });

    it("accepts message with name and toolCallId for tool role", () => {
      const msg: ChatMessage = {
        role: "tool",
        content: "The weather is sunny",
        name: "get_weather",
        toolCallId: "tc_1",
      };
      expect(msg.name).toBe("get_weather");
      expect(msg.toolCallId).toBe("tc_1");
    });
  });

  describe("ToolCall", () => {
    it("represents a tool call with arguments", () => {
      const tc: ToolCall = {
        id: "tc_123",
        name: "search",
        arguments: { query: "test query", limit: 5 },
      };
      expect(tc.id).toBe("tc_123");
      expect(tc.name).toBe("search");
      expect(tc.arguments).toEqual({ query: "test query", limit: 5 });
    });
  });

  describe("ToolDefinition", () => {
    it("represents a tool with parameters", () => {
      const tool: ToolDefinition = {
        name: "get_weather",
        description: "Get weather for a location",
        parameters: {
          type: "object",
          properties: {
            location: { type: "string", description: "City name" },
            unit: {
              type: "string",
              description: "Temperature unit",
              enum: ["celsius", "fahrenheit"],
            },
          },
          required: ["location"],
        },
      };
      expect(tool.name).toBe("get_weather");
      expect(tool.parameters.required).toContain("location");
      expect(tool.parameters.properties.location.type).toBe("string");
    });
  });

  describe("ChatResponse", () => {
    it("represents a basic chat response", () => {
      const resp: ChatResponse = {
        content: "Hello!",
        model: "gpt-4",
        finishReason: "stop",
      };
      expect(resp.content).toBe("Hello!");
      expect(resp.finishReason).toBe("stop");
    });

    it("represents a response with tool calls", () => {
      const resp: ChatResponse = {
        content: "I'll search for that",
        model: "gpt-4",
        finishReason: "tool_calls",
        toolCalls: [
          { id: "tc_1", name: "search", arguments: { query: "AI news" } },
        ],
      };
      expect(resp.finishReason).toBe("tool_calls");
      expect(resp.toolCalls).toHaveLength(1);
    });

    it("represents a response with token usage", () => {
      const resp: ChatResponse = {
        content: "Response text",
        model: "claude-3",
        finishReason: "stop",
        usage: { promptTokens: 100, completionTokens: 50, totalTokens: 150 },
      };
      expect(resp.usage!.totalTokens).toBe(150);
    });
  });

  describe("StreamChunk", () => {
    it("represents a stream chunk", () => {
      const chunk: StreamChunk = {
        content: "Hello",
        done: false,
      };
      expect(chunk.content).toBe("Hello");
      expect(chunk.done).toBe(false);
    });

    it("represents a final stream chunk", () => {
      const chunk: StreamChunk = {
        content: "",
        done: true,
      };
      expect(chunk.done).toBe(true);
    });
  });

  describe("StreamEvent", () => {
    it("represents text_delta event", () => {
      const event: StreamEvent = { type: "text_delta", content: "Hello" };
      expect(event.type).toBe("text_delta");
    });

    it("represents tool_start event", () => {
      const event: StreamEvent = {
        type: "tool_start",
        toolCallId: "tc_1",
        toolName: "search",
      };
      expect(event.type).toBe("tool_start");
    });

    it("represents tool_args_delta event", () => {
      const event: StreamEvent = {
        type: "tool_args_delta",
        toolCallId: "tc_1",
        argsDelta: '{"query":',
      };
      expect(event.type).toBe("tool_args_delta");
    });

    it("represents tool_end event", () => {
      const event: StreamEvent = {
        type: "tool_end",
        toolCallId: "tc_1",
        toolName: "search",
        arguments: { query: "test" },
      };
      expect(event.type).toBe("tool_end");
    });

    it("represents done event with usage", () => {
      const event: StreamEvent = {
        type: "done",
        usage: { promptTokens: 10, completionTokens: 20, totalTokens: 30 },
      };
      expect(event.type).toBe("done");
    });
  });

  describe("TokenUsage", () => {
    it("represents token usage", () => {
      const usage = {
        promptTokens: 100,
        completionTokens: 50,
        totalTokens: 150,
      };
      expect(usage.promptTokens).toBe(100);
      expect(usage.completionTokens).toBe(50);
      expect(usage.totalTokens).toBe(150);
    });
  });

  describe("EmbeddingResponse", () => {
    it("represents an embedding response", () => {
      const resp: EmbeddingResponse = {
        embedding: [0.1, 0.2, 0.3],
        model: "nomic-embed-text",
      };
      expect(resp.embedding).toHaveLength(3);
      expect(resp.model).toBe("nomic-embed-text");
    });
  });

  describe("ProviderConfig", () => {
    it("represents a minimal config", () => {
      const config: ProviderConfig = { name: "ollama" };
      expect(config.name).toBe("ollama");
    });

    it("represents a full config", () => {
      const config: ProviderConfig = {
        name: "openai",
        baseUrl: "https://api.openai.com/v1",
        apiKey: "sk-test",
        defaultModel: "gpt-4-turbo",
        defaultEmbeddingModel: "text-embedding-3-small",
        options: { timeout: 60000 },
      };
      expect(config.baseUrl).toBe("https://api.openai.com/v1");
      expect(config.apiKey).toBe("sk-test");
    });
  });

  describe("ChatOptions", () => {
    it("represents minimal options", () => {
      const opts: ChatOptions = {};
      expect(opts).toEqual({});
    });

    it("represents options with temperature and maxTokens", () => {
      const opts: ChatOptions = {
        temperature: 0.7,
        maxTokens: 1000,
        topP: 0.9,
        stop: ["\n\n"],
        raw: { presence_penalty: 0.5 },
      };
      expect(opts.temperature).toBe(0.7);
      expect(opts.maxTokens).toBe(1000);
      expect(opts.stop).toContain("\n\n");
    });
  });
});

describe("ModelProvider Interface", () => {
  it("mock provider implements ModelProvider interface", () => {
    const provider = makeMockProvider();
    expect(provider.name).toBe("mock");
    expect(typeof provider.chat).toBe("function");
    expect(typeof provider.chatWithTools).toBe("function");
    expect(typeof provider.chatStream).toBe("function");
    expect(typeof provider.embed).toBe("function");
    expect(typeof provider.listModels).toBe("function");
    expect(typeof provider.healthCheck).toBe("function");
  });

  it("mock provider chat returns ChatResponse", async () => {
    const provider = makeMockProvider();
    const response = await provider.chat(mockMessages);
    expect(response).toHaveProperty("content");
    expect(response).toHaveProperty("model");
    expect(response).toHaveProperty("finishReason");
  });

  it("mock provider chatWithTools returns ChatResponse with toolCalls", async () => {
    const provider = makeMockProvider();
    const response = await provider.chatWithTools(mockMessages, mockTools);
    expect(response).toHaveProperty("content");
    expect(response.finishReason).toBe("stop");
  });

  it("mock provider embed returns EmbeddingResponse", async () => {
    const provider = makeMockProvider();
    const response = await provider.embed("test text");
    expect(response).toHaveProperty("embedding");
    expect(response).toHaveProperty("model");
  });

  it("mock provider listModels returns string array", async () => {
    const provider = makeMockProvider();
    const models = await provider.listModels();
    expect(Array.isArray(models)).toBe(true);
  });

  it("mock provider healthCheck returns boolean", async () => {
    const provider = makeMockProvider();
    const result = await provider.healthCheck();
    expect(typeof result).toBe("boolean");
  });
});

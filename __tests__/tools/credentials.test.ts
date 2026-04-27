import { describe, it, expect } from "vitest";
import { CredentialsTool } from "../../src/tools/credentials.js";
import { join } from "node:path";

describe("CredentialsTool", () => {
  const testCwd = join(__dirname, "test-data");

  it("should have correct definition shape", () => {
    expect(CredentialsTool.definition.name).toBe("credentials_get");
    expect(CredentialsTool.definition.description).toContain("credential");
    expect(CredentialsTool.definition.parameters.properties.key).toBeDefined();
    expect(CredentialsTool.definition.parameters.properties.owlName).toBeDefined();
  });

  it("should return credential value when key exists", async () => {
    const result = await CredentialsTool.execute(
      { key: "TEST_KEY", owlName: "TradingBot" },
      { cwd: testCwd },
    );
    const parsed = JSON.parse(result);
    expect(parsed.value).toBe("test_value");
  });

  it("should return error when key not found", async () => {
    const result = await CredentialsTool.execute(
      { key: "NONEXISTENT", owlName: "TradingBot" },
      { cwd: testCwd },
    );
    const parsed = JSON.parse(result);
    expect(parsed.error).toBeDefined();
    expect(parsed.error).toContain("not found");
  });

  it("should return error when owl credentials folder does not exist", async () => {
    const result = await CredentialsTool.execute(
      { key: "TEST_KEY", owlName: "NonExistentOwl" },
      { cwd: testCwd },
    );
    const parsed = JSON.parse(result);
    expect(parsed.error).toBeDefined();
  });

  it("should require key and owlName parameters", async () => {
    const result = await CredentialsTool.execute(
      {},
      { cwd: testCwd },
    );
    const parsed = JSON.parse(result);
    expect(parsed.error).toContain("Missing");
  });
});

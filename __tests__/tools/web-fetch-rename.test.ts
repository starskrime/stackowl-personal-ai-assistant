import { describe, it, expect } from "vitest";
import { WebFetchTool } from "../../src/tools/web.js";

describe("web.ts — rename to web_fetch", () => {
  it("exports WebFetchTool with name 'web_fetch' and not deprecated", () => {
    expect(WebFetchTool.definition.name).toBe("web_fetch");
    expect(WebFetchTool.definition.deprecated).toBeFalsy();
  });

  it("description mentions hint:'anti-bot' parameter", () => {
    expect(WebFetchTool.definition.description.toLowerCase()).toContain("anti-bot");
  });

  it("parameters expose hint as enum['anti-bot']", () => {
    const params = WebFetchTool.definition.parameters as any;
    expect(params.properties.hint?.enum).toEqual(["anti-bot"]);
  });
});

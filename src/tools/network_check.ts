import { BaseSkill, SkillResult } from "../schema";
import { ShellCommandTool } from "./shell";

export class NetworkCheckSkill extends BaseSkill {
  name = "network_check";
  description = "Check network connectivity by pinging hosts and resolving DNS.";

  async execute(params: {
    host?: string;
    check_type?: "ping" | "dns" | "all";
  }): Promise<SkillResult> {
    const host = params.host || "8.8.8.8";
    const check_type = params.check_type || "all";

    const results: Record<string, unknown> = {};
    const shell = new ShellCommandTool();

    // Ping check
    if (check_type === "ping" || check_type === "all") {
      try {
        const pingResult = await shell.call({
          command: `ping -c 3 ${host}`,
          mode: "sandbox",
        });
        results.ping = pingResult;
      } catch (e) {
        results.ping = { error: String(e) };
      }
    }

    // DNS check
    if (check_type === "dns" || check_type === "all") {
      try {
        const dnsResult = await shell.call({
          command: `nslookup google.com`,
          mode: "sandbox",
        });
        results.dns = dnsResult;
      } catch (e) {
        results.dns = { error: String(e) };
      }
    }

    return {
      success: true,
      message: "Network check complete",
      data: results,
    };
  }
}

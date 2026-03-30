---
name: privacy_audit
description: Audit macOS privacy and security settings including firewall, FileVault, SIP, and app permissions
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🕵️"
  os: [darwin]
parameters: {}
required: []
steps:
  - id: check_firewall
    tool: ShellTool
    args:
      command: "sudo /usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null || echo 'Requires admin privileges'"
      mode: "local"
  - id: check_filevault
    tool: ShellTool
    args:
      command: "fdesetup status"
      mode: "local"
  - id: check_sip
    tool: ShellTool
    args:
      command: "csrutil status"
      mode: "local"
  - id: check_gatekeeper
    tool: ShellTool
    args:
      command: "spctl --status"
      mode: "local"
  - id: generate_report
    type: llm
    prompt: "Based on the audit results, create a privacy and security report with recommendations:\n\nFirewall: {{check_firewall.output}}\nFileVault: {{check_filevault.output}}\nSIP: {{check_sip.output}}\nGatekeeper: {{check_gatekeeper.output}}\n\nNote any issues and suggest improvements."
    depends_on: [check_firewall, check_filevault, check_sip, check_gatekeeper]
    inputs: [check_firewall.output, check_filevault.output, check_sip.output, check_gatekeeper.output]
---

# Privacy Audit

Audit macOS privacy/security settings.

## Usage

```bash
/privacy_audit
```

## Examples

### Full audit

```
Checks: Firewall, FileVault, SIP, Gatekeeper
```

## Error Handling

- **Requires sudo:** Note which checks need admin privileges.

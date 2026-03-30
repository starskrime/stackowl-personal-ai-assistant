---
name: system_info
description: Display comprehensive macOS system information including CPU, RAM, disk, OS version, and uptime
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "💻"
  os: [darwin]
steps:
  - id: hardware_info
    tool: ShellTool
    args:
      command: "system_profiler SPHardwareDataType 2>/dev/null | grep -E 'Model|Chip|Memory|Serial'"
      mode: "local"
    timeout_ms: 10000
  - id: os_version
    tool: ShellTool
    args:
      command: "sw_vers"
      mode: "local"
    timeout_ms: 5000
  - id: system_uptime
    tool: ShellTool
    args:
      command: "uptime"
      mode: "local"
    timeout_ms: 5000
  - id: disk_usage
    tool: ShellTool
    args:
      command: "df -h / | tail -1"
      mode: "local"
    timeout_ms: 5000
  - id: cpu_status
    tool: ShellTool
    args:
      command: "top -l 1 | head -10"
      mode: "local"
    timeout_ms: 5000
  - id: summarize
    type: llm
    prompt: "Format these system information results into a clean summary with sections for macOS version and build, CPU/chip model, RAM total and used, disk usage (used/total), and uptime:\n\nHardware: {{hardware_info.output}}\nOS: {{os_version.output}}\nUptime: {{system_uptime.output}}\nDisk: {{disk_usage.output}}\nCPU: {{cpu_status.output}}"
    depends_on: [hardware_info, os_version, system_uptime, disk_usage, cpu_status]
    inputs: [hardware_info.output, os_version.output, system_uptime.output, disk_usage.output, cpu_status.output]
---

# System Info

Get detailed system status on macOS.

## Usage

```bash
/system_info
```

## Error Handling

- **system_profiler slow:** Use faster alternatives like `sysctl`.
- **Permission denied:** Some info requires admin access; skip and note.

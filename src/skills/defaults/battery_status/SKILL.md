---
name: battery_status
description: Check battery charge level, health, cycle count, and power source on macOS laptops
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔋"
  os: [darwin]
parameters: {}
required: []
steps:
  - id: get_battery_info
    tool: ShellTool
    args:
      command: "pmset -g batt"
      mode: "local"
    timeout_ms: 5000
  - id: get_battery_health
    tool: ShellTool
    args:
      command: "system_profiler SPPowerDataType 2>/dev/null | grep -E 'Charge|Cycle|Condition|Connected|Power Source State'"
      mode: "local"
    timeout_ms: 10000
  - id: parse_battery
    type: llm
    prompt: "Parse the battery information and summarize: current charge percentage, time remaining (if on battery), cycle count, battery condition, and power source state. If no battery exists, note that it's running on AC power."
    depends_on: [get_battery_info, get_battery_health]
    inputs: [get_battery_info.stdout, get_battery_health.stdout]
---

# Battery Status

Display battery information on macOS.

## Steps

1. **Get battery info:**
   ```bash
   pmset -g batt
   system_profiler SPPowerDataType 2>/dev/null | grep -E 'Charge|Cycle|Condition|Connected'
   ```
2. **Present summary:** charge %, time remaining, cycle count, health condition.

## Examples

### Quick check

```bash
pmset -g batt
```

## Error Handling

- **Desktop Mac:** Report "No battery — running on AC power."

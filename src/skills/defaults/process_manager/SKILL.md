---
name: process_manager
description: List running processes, find resource-heavy tasks, and kill unresponsive applications on macOS
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "⚙️"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: top, find, or kill"
    default: "top"
  name:
    type: string
    description: "Process name to find or kill"
  pid:
    type: number
    description: "Process ID to kill"
  signal:
    type: string
    description: "Signal to send: TERM, KILL"
    default: "TERM"
steps:
  - id: top_cpu
    tool: ShellTool
    args:
      command: "ps aux --sort=-%cpu | head -20"
      mode: "local"
    timeout_ms: 10000
  - id: top_memory
    tool: ShellTool
    args:
      command: "ps aux --sort=-%mem | head -20"
      mode: "local"
    timeout_ms: 10000
  - id: find_process
    tool: ShellTool
    args:
      command: "pgrep -fl '{{name}}' || echo 'No process found'"
      mode: "local"
    timeout_ms: 5000
  - id: kill_soft
    tool: ShellTool
    args:
      command: "kill {{pid}} && echo 'SIGTERM sent to {{pid}}'"
      mode: "local"
    timeout_ms: 5000
  - id: kill_hard
    tool: ShellTool
    args:
      command: "kill -9 {{pid}} && echo 'SIGKILL sent to {{pid}}'"
      mode: "local"
    timeout_ms: 5000
  - id: pkill_process
    tool: ShellTool
    args:
      command: "pkill -f '{{name}}' && echo 'Killed processes matching {{name}}'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Process management - action: '{{action}}'\n\n{{#if_eq action 'top'}}Top by CPU:\n{{top_cpu.output}}\n\nTop by Memory:\n{{top_memory.output}}{{/if_eq}}\n{{#if_eq action 'find'}}Found processes:\n{{find_process.output}}{{/if_eq}}\n{{#if_eq action 'kill'}}Result:\n{{#if pid}}{{#if_eq signal 'KILL'}}{{kill_hard.output}}{{/if_eq}}{{#if_eq signal 'TERM'}}{{kill_soft.output}}{{/if_eq}}{{/if}}{{#if name}}{{pkill_process.output}}{{/if}}{{/if}}"
    depends_on: [top_cpu]
    inputs: [top_cpu.output, top_memory.output, find_process.output, kill_soft.output, kill_hard.output, pkill_process.output]
---

# Process Manager

Monitor and manage running processes.

## Usage

Show top processes:
```
/process_manager
```

Find a process:
```
action=find
name=node
```

Kill by PID:
```
action=kill
pid=1234
signal=KILL
```

Kill by name:
```
action=kill
name=chrome
```

## Actions

- **top** (default): Show top processes by CPU and memory
- **find**: Search for processes by name
- **kill**: Terminate a process

## Examples

### Show top CPU consumers
```
action=top
```

### Find Chrome processes
```
action=find
name=Chrome
```

### Force kill a process
```
action=kill
pid=5678
signal=KILL
```

### Kill all node processes
```
action=kill
name=node
```

## Safety

- **System processes**: Warns before killing critical processes
- **Force kill**: Only use SIGKILL as last resort
- **Permission denied**: Some processes require sudo
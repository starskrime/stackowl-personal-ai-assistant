---
name: time_tracker
description: Track time spent on tasks by starting and stopping a timer, with daily summaries logged to a file
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "⏱️"
parameters:
  action:
    type: string
    description: "Action: start, stop, status, or summary"
    default: "status"
  task:
    type: string
    description: "Task name to track"
  file:
    type: string
    description: "Log file path"
    default: "~/stackowl_timetrack.csv"
steps:
  - id: init_file
    tool: ShellTool
    args:
      command: "touch {{file}} && head -1 {{file}} 2>/dev/null || echo 'TYPE,TIMESTAMP,TASK' > {{file}}"
      mode: "local"
    timeout_ms: 5000
  - id: start_timer
    tool: ShellTool
    args:
      command: "echo \"START,$(date +%Y-%m-%dT%H:%M:%S),{{task}}\" >> {{file}} && echo \"Timer started for: {{task}}\""
      mode: "local"
    timeout_ms: 5000
  - id: stop_timer
    tool: ShellTool
    args:
      command: "echo \"STOP,$(date +%Y-%m-%dT%H:%M:%S),{{task}}\" >> {{file}} && echo \"Timer stopped for: {{task}}\""
      mode: "local"
    timeout_ms: 5000
  - id: get_status
    tool: ShellTool
    args:
      command: "grep 'START.*{{task}}.*' {{file}} | tail -1 | grep -v STOP && echo 'Running' || echo 'Not running'"
      mode: "local"
    timeout_ms: 5000
  - id: show_entries
    tool: ShellTool
    args:
      command: "grep -E 'START|STOP' {{file}} | tail -20"
      mode: "local"
    timeout_ms: 5000
  - id: daily_summary
    tool: ShellTool
    args:
      command: "awk -F',' '$1==\"START\" {s=$3; t=$2} $1==\"STOP\" && $3==s {print $2,s,($2>t?\"\":\"invalid\"),\"duration:\"int((systime()-s)/60)\"min\"; s=\"\"} END {if(s) print \"Running:\",s,t}' {{file}}"
      mode: "local"
    timeout_ms: 10000
  - id: analyze
    type: llm
    prompt: "Time tracker - action: '{{action}}'\n\n{{#if_eq action 'status'}}Active timer: {{get_status.output}}{{/if_eq}}\n{{#if_eq action 'start'}}Timer started for: {{task}}{{/if_eq}}\n{{#if_eq action 'stop'}}Timer stopped for: {{task}}{{/if_eq}}\n\nRecent entries:\n{{show_entries.output}}\n\nDaily summary:\n{{daily_summary.output}}"
    depends_on: [init_file]
    inputs: [show_entries.output, daily_summary.output, get_status.output]
---

# Time Tracker

Track time spent on tasks.

## Usage

Start a timer:
```
action=start
task=coding_project
```

Stop the timer:
```
action=stop
task=coding_project
```

Check status:
```
action=status
task=coding_project
```

Daily summary:
```
action=summary
```

## Actions

- **start**: Begin tracking a task
- **stop**: End tracking for a task
- **status**: Check if a task is currently being tracked
- **summary**: Show all tracked time today

## Examples

### Start coding timer
```
action=start
task=coding
```

### Stop and log time
```
action=stop
task=coding
```

### View today's summary
```
action=summary
```

## Notes

- Times are logged to CSV file
- Tasks must be explicitly stopped
- Summary shows duration for completed tasks
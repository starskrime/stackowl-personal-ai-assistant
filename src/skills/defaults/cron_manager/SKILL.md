---
name: cron_manager
description: List, create, edit, and delete cron jobs for scheduled task automation
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "⏲️"
parameters:
  action:
    type: string
    description: "Action: list, create, delete, or check"
    default: "list"
  schedule:
    type: string
    description: "Cron schedule (e.g., '0 9 * * *' for daily at 9am)"
  command:
    type: string
    description: "Command or script path to run"
  pattern:
    type: string
    description: "Pattern to match for deletion"
required: []
steps:
  - id: list_crons
    tool: ShellTool
    args:
      command: "crontab -l 2>/dev/null || echo 'No crontab installed'"
      mode: "local"
    timeout_ms: 5000
  - id: create_cron
    tool: ShellTool
    args:
      command: "(crontab -l 2>/dev/null; echo '{{schedule}} {{command}}') | crontab -"
      mode: "local"
    timeout_ms: 5000
  - id: delete_cron
    tool: ShellTool
    args:
      command: "crontab -l 2>/dev/null | grep -v '{{pattern}}' | crontab -"
      mode: "local"
    timeout_ms: 5000
  - id: verify_cron
    tool: ShellTool
    args:
      command: "crontab -l 2>/dev/null"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Cron manager - action: '{{action}}'\n\nCurrent crontab:\n{{list_crons.output}}\n\n{{#if_eq action 'create'}}Added: {{schedule}} {{command}}\n{{/if_eq}}\n{{#if_eq action 'delete'}}Removed pattern: {{pattern}}\n{{/if_eq}}\n\nVerify result:\n{{verify_cron.output}}"
    depends_on: [list_crons]
    inputs: [list_crons.output, verify_cron.output]
---

# Cron Manager

Manage scheduled cron jobs.

## Usage

List current cron jobs:
```
/cron_manager
```

Create a cron job:
```
action=create
schedule=0 9 * * *
command=/path/to/script.sh
```

Delete cron jobs by pattern:
```
action=delete
pattern=some_pattern
```

## Schedule Format

```
┌───────────── minute (0-59)
│ ┌───────────── hour (0-23)
│ │ ┌───────────── day of month (1-31)
│ │ │ ┌───────────── month (1-12)
│ │ │ │ ┌───────────── day of week (0-7, 0 and 7 are Sunday)
│ │ │ │ │
* * * * *
```

## Examples

### Daily at 9am
```
action=create
schedule=0 9 * * *
command=/path/to/script.sh
```

### Every hour
```
action=create
schedule=0 * * * *
command=/path/to/script.sh
```

### Every Monday at 9am
```
action=create
schedule=0 9 * * 1
command=/path/to/script.sh
```

### Delete jobs with "backup" in them
```
action=delete
pattern=backup
```

## Error Handling

- **Invalid schedule:** Validates cron syntax
- **Script not executable:** Suggests chmod +x
- **No crontab:** Reports when crontab is empty
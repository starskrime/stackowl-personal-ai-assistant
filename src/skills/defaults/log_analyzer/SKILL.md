---
name: log_analyzer
description: Analyze log files to find errors, patterns, frequency distributions, and anomalies
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🪵"
parameters:
  file:
    type: string
    description: "Path to log file"
  pattern:
    type: string
    description: "Pattern to search for (e.g., ERROR, WARNING)"
    default: "ERROR"
  lines:
    type: number
    description: "Number of tail lines to analyze"
    default: 100
required: [file]
steps:
  - id: check_file
    tool: ShellTool
    args:
      command: "ls -la {{file}} && wc -l {{file}}"
      mode: "local"
    timeout_ms: 5000
  - id: read_tail
    tool: ShellTool
    args:
      command: "tail -{{lines}} {{file}}"
      mode: "local"
    timeout_ms: 10000
  - id: count_errors
    tool: ShellTool
    args:
      command: "grep -i '{{pattern}}' {{file}} | wc -l"
      mode: "local"
    timeout_ms: 10000
  - id: top_errors
    tool: ShellTool
    args:
      command: "grep -i '{{pattern}}' {{file}} | sed 's/[[:space:]]*$//' | sort | uniq -c | sort -rn | head -15"
      mode: "local"
    timeout_ms: 15000
  - id: time_patterns
    tool: ShellTool
    args:
      command: "grep -i '{{pattern}}' {{file}} | awk '{print $1, $2}' | cut -d: -f1-2 | sort | uniq -c | sort -rn | head -10"
      mode: "local"
    timeout_ms: 15000
  - id: recent_errors
    tool: ShellTool
    args:
      command: "grep -i '{{pattern}}' {{file}} | tail -10"
      mode: "local"
    timeout_ms: 10000
  - id: analyze
    type: llm
    prompt: "Log analysis for: {{file}}\nPattern: {{pattern}}\n\nTotal occurrences: {{count_errors}}\n\nTop error messages:\n{{top_errors}}\n\nTime distribution:\n{{time_patterns}}\n\nRecent entries:\n{{recent_errors}}"
    depends_on: [count_errors]
    inputs: [count_errors.output, top_errors.output, time_patterns.output, recent_errors.output]
---

# Log Analyzer

Analyze log files for errors, patterns, and anomalies.

## Usage

```bash
/log_analyzer /var/log/app.log
```

With options:
```
file=/var/log/app.log
pattern=ERROR
lines=200
```

## Parameters

- **file**: Path to log file
- **pattern**: Search pattern (default: ERROR)
- **lines**: Number of tail lines to analyze

## Examples

### Find errors
```
file=/var/log/app.log
pattern=ERROR
```

### Find warnings
```
file=/var/log/app.log
pattern=WARNING
```

### Analyze last 500 lines
```
file=./debug.log
lines=500
pattern=error
```

## Output

- **Count**: Total pattern occurrences
- **Top errors**: Most common messages
- **Time patterns**: When errors occur
- **Recent entries**: Last matching lines
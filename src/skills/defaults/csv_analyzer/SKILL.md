---
name: csv_analyzer
description: Analyze CSV files by showing statistics, column summaries, row counts, and data patterns
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📊"
parameters:
  file_path:
    type: string
    description: "Path to CSV file to analyze"
  column:
    type: number
    description: "Column number to analyze (1-indexed)"
    default: "1"
required: [file_path]
steps:
  - id: read_sample
    tool: ShellTool
    args:
      command: "head -5 {{file_path}}"
      mode: "local"
    timeout_ms: 5000
  - id: count_rows
    tool: ShellTool
    args:
      command: "wc -l {{file_path}}"
      mode: "local"
    timeout_ms: 5000
  - id: list_columns
    tool: ShellTool
    args:
      command: "head -1 {{file_path}} | tr ',' '\n' | nl"
      mode: "local"
    timeout_ms: 5000
  - id: column_stats
    tool: ShellTool
    args:
      command: "awk -F',' '{print ${{column}}}}' {{file_path}} | sort | uniq -c | sort -rn | head -10"
      mode: "local"
    timeout_ms: 10000
  - id: analyze_data
    type: llm
    prompt: "Analyze this CSV file and provide a summary with:\n- Row count and column count\n- Sample data (first 5 rows): {{read_sample.output}}\n- Column listing:\n{{list_columns.output}}\n- Top values in column {{column}}:\n{{column_stats.output}}\n- Note any missing values or data patterns\n\nTotal rows: {{count_rows.output}}"
    depends_on: [read_sample, count_rows, list_columns, column_stats]
    inputs: [read_sample.output, count_rows.output, list_columns.output, column_stats.output]
---

# CSV Analyzer

Analyze CSV data files.

## Usage

```bash
/csv_analyzer file_path="sales.csv"
```

## Parameters

- **file_path**: Path to CSV file to analyze
- **column**: Column number to analyze (1-indexed, default: 1)

## Examples

```
csv_analyzer file_path="sales.csv" column=3
```

## Error Handling

- **Not a valid CSV:** Check delimiter (could be TSV or semicolon-separated).
- **Very large file:** Use `head` and sampling instead of reading entire file.

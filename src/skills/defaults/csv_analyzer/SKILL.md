---
name: csv_analyzer
description: Analyze CSV files by showing statistics, column summaries, row counts, and data patterns
openclaw:
  emoji: "📊"
---

# CSV Analyzer

Analyze CSV data files.

## Steps

1. **Read file header and sample:**
   ```bash
   run_shell_command("head -5 <file.csv>")
   run_shell_command("wc -l <file.csv>")
   ```
2. **Get column info:**
   ```bash
   run_shell_command("head -1 <file.csv> | tr ',' '\n' | nl")
   ```
3. **Compute basic statistics:**
   ```bash
   run_shell_command("awk -F',' '{print $<col>}' <file.csv> | sort | uniq -c | sort -rn | head -10")
   ```
4. **Present summary:** row count, column count, unique values per column, missing values.

## Examples

### Analyze a sales CSV

```bash
run_shell_command("head -5 sales.csv && wc -l sales.csv")
```

## Error Handling

- **Not a valid CSV:** Check delimiter (could be TSV or semicolon-separated).
- **Very large file:** Use `head` and sampling instead of reading entire file.

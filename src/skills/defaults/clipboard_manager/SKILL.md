---
name: clipboard_manager
description: Read, write, or transform the contents of the macOS clipboard (pasteboard)
openclaw:
  emoji: "📋"
  os: [darwin]
---

# Clipboard Manager

Manage macOS clipboard contents.

## Steps

1. **Read clipboard:**
   ```bash
   run_shell_command("pbpaste")
   ```
2. **Write to clipboard:**
   ```bash
   run_shell_command("echo '<text>' | pbcopy")
   ```
3. **Transform clipboard** (e.g., uppercase, sort lines):
   ```bash
   run_shell_command("pbpaste | tr '[:lower:]' '[:upper:]' | pbcopy")
   ```

## Examples

### Copy text to clipboard

```bash
run_shell_command("echo 'Hello World' | pbcopy")
```

### Read and transform

```bash
run_shell_command("pbpaste | sort | pbcopy")
```

## Error Handling

- **Empty clipboard:** Inform user "Clipboard is empty."
- **Binary content:** Note that only text content can be displayed.

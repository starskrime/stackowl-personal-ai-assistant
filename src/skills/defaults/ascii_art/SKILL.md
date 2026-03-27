---
name: ascii_art
description: Generate ASCII art text banners from input text using figlet or toilet commands
openclaw:
  emoji: "🔤"
---

# ASCII Art Generator

Create ASCII art text banners.

## Steps

1. **Check for tools:**
   ```bash
   run_shell_command("which figlet || which toilet")
   ```
2. **Generate ASCII art:**
   ```bash
   run_shell_command("figlet '<text>'")
   ```
   Or with style: `run_shell_command("figlet -f slant '<text>'")`
3. **Present** the ASCII art.

## Examples

### Create a banner

```bash
run_shell_command("figlet 'Hello World'")
```

## Error Handling

- **figlet not installed:** `brew install figlet`.
- **Font not found:** List available fonts with `figlet -list`.

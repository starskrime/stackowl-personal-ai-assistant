---
name: ascii_art
description: Generate ASCII art text banners from input text using figlet or toilet commands
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔤"
parameters:
  text:
    type: string
    description: "Text to convert to ASCII art"
  font:
    type: string
    description: "Font style (e.g., slant, standard, small)"
    default: "standard"
required: [text]
steps:
  - id: check_tools
    tool: ShellTool
    args:
      command: "which figlet || which toilet"
      mode: "local"
    timeout_ms: 5000
  - id: generate_art
    tool: ShellTool
    args:
      command: "figlet -f {{font}} '{{text}}'"
      mode: "local"
    timeout_ms: 10000
  - id: present_art
    type: llm
    prompt: "Present the ASCII art generated for '{{text}}' using font '{{font}}'.\n\nOutput:\n{{generate_art.output}}\n\nIf figlet was not found, suggest installing with `brew install figlet`."
    depends_on: [generate_art]
    inputs: [generate_art.output]
---

# ASCII Art Generator

Create ASCII art text banners.

## Usage

```bash
/ascii_art text="Hello World"
```

## Parameters

- **text**: Text to convert to ASCII art
- **font**: Font style (e.g., slant, standard, small, default: standard)

## Examples

```
ascii_art text="Hello World"
ascii_art text="Welcome" font=slant
```

## Error Handling

- **figlet not installed:** `brew install figlet`.
- **Font not found:** List available fonts with `figlet -list`.

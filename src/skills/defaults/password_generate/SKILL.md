---
name: password_generate
description: Generate cryptographically secure random passwords with configurable length and character requirements
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔐"
parameters:
  length:
    type: number
    description: "Password length in characters"
    default: 20
  charset:
    type: string
    description: "Character set to use for password generation"
    default: "A-Za-z0-9!@#$%^&*"
  copy_to_clipboard:
    type: boolean
    description: "Whether to copy password to clipboard"
    default: true
required: [length]
steps:
  - id: generate_password
    tool: ShellTool
    args:
      command: "LC_ALL=C tr -dc '{{charset}}' < /dev/urandom | head -c {{length}}"
      mode: "local"
  - id: copy_clipboard
    tool: ShellTool
    args:
      command: "echo -n '{{generate_password.output}}' | pbcopy"
      mode: "local"
    optional: true
    depends_on: [generate_password]
---

# Password Generator

Generate secure random passwords.

## Usage

```bash
/password_generate length=24 charset=A-Za-z0-9!@#$%^&* copy_to_clipboard=true
/password_generate length=32
```

## Parameters

- **length**: Password length in characters (required, default: 20)
- **charset**: Character set to use (default: A-Za-z0-9!@#$%^&*)
- **copy_to_clipboard**: Whether to copy password to clipboard (default: true)

## Examples

### Generate 24-char password

```bash
LC_ALL=C tr -dc 'A-Za-z0-9!@#$%^&*' < /dev/urandom | head -c 24
```

### Generate 32-char password with OpenSSL

```bash
openssl rand -base64 32 | head -c 32
```

## Error Handling

- **Clipboard not available:** Display the password directly (warn about screen visibility).

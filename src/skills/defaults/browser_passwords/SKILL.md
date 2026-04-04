---
name: browser_passwords
description: View and manage saved browser passwords for Safari and Chrome (requires authentication)
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔑"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: list, search, or export"
    default: "list"
  browser:
    type: string
    description: "Browser: safari, chrome"
    default: "safari"
  domain:
    type: string
    description: "Domain to search (e.g., github.com)"
required: []
steps:
  - id: safari_passwords
    tool: ShellTool
    args:
      command: "security find-internet-password -s '{{domain}}' 2>/dev/null | head -20 || echo 'No passwords found or permission denied'"
    mode: "local"
    timeout_ms: 10000
  - id: chrome_passwords
    tool: ShellTool
    args:
      command: "echo 'Chrome passwords require decryption - use Keychain Access or Safari to view'"
    mode: "local"
    timeout_ms: 5000
  - id: search_keychain
    tool: ShellTool
    args:
      command: "security find-generic-password -s '{{domain}}' 2>/dev/null | head -20"
    mode: "local"
    timeout_ms: 10000
  - id: open_keychain
    tool: ShellTool
    args:
      command: "open -a 'Keychain Access'"
    mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Browser passwords for {{browser}}:\n\nResults:\n{{search_keychain.output}}\n\nTo view passwords, use Keychain Access app."
    depends_on: [search_keychain]
    inputs: [search_keychain.output]
---

# Browser Passwords

Manage saved browser passwords.

## Usage

Search passwords:
```
action=search
domain=github.com
```

Open Keychain:
```
action=list
```

## Security Note

**Passwords are sensitive!** This skill only shows if you have permission.

## Actions

- **list**: Show saved passwords
- **search**: Search by domain
- **export**: Export (requires permission)

## Examples

### Find GitHub passwords
```
action=search
domain=github.com
```

### Open Keychain Access
```
action=list
```

## Notes

- macOS Keychain stores all passwords
- Safari uses Keychain
- Chrome has its own password manager
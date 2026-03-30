---
name: ssh_connect
description: Connect to remote servers via SSH, manage SSH keys, and execute remote commands
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔑"
parameters:
  host:
    type: string
    description: "SSH host (user@hostname or just hostname)"
  command:
    type: string
    description: "Command to execute remotely (leave empty for interactive session)"
    default: ""
  key_email:
    type: string
    description: "Email for SSH key comment"
    default: ""
required: [host]
steps:
  - id: check_connection
    tool: ShellTool
    args:
      command: "ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no {{host}} 'echo Connected' 2>&1"
      mode: "local"
    timeout_ms: 10000
  - id: execute_command
    tool: ShellTool
    args:
      command: "ssh {{host}} '{{command}}'"
      mode: "local"
    timeout_ms: 30000
    optional: true
  - id: present_result
    type: llm
    prompt: "Present the SSH connection result clearly.\n\nConnection output: {{check_connection.output}}\n{{#if command}}Command output: {{execute_command.output}}{{/if}}"
    depends_on: [check_connection, execute_command]
    inputs: [check_connection.output, execute_command.output]
---

# SSH Connect

Manage SSH connections and keys.

## Usage

```bash
/ssh_connect host=user@server.com
/ssh_connect host=user@server.com command="uptime && df -h"
```

## Parameters

- **host**: SSH host (user@hostname or just hostname) (required)
- **command**: Command to execute remotely (leave empty for interactive session)
- **key_email**: Email for SSH key comment

## Examples

```
ssh_connect host=user@server.com command="uptime && df -h"
```

## Error Handling

- **Connection refused:** Check if SSH is running on the host (port 22).
- **Permission denied:** Key may not be authorized; use `ssh-copy-id`.
- **Host key verification:** Accept on first connect or check `~/.ssh/known_hosts`.

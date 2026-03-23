---
name: ssh_connect
description: Connect to remote servers via SSH, manage SSH keys, and execute remote commands
openclaw:
  emoji: "🔑"
---
# SSH Connect
Manage SSH connections and keys.
## Steps
1. **Connect to server:**
   ```bash
   run_shell_command("ssh <user>@<host>")
   ```
2. **Execute remote command:**
   ```bash
   run_shell_command("ssh <user>@<host> '<command>'")
   ```
3. **Generate SSH key:**
   ```bash
   run_shell_command("ssh-keygen -t ed25519 -C '<email>'")
   ```
4. **Copy key to server:**
   ```bash
   run_shell_command("ssh-copy-id <user>@<host>")
   ```
## Examples
### Run remote command
```bash
run_shell_command("ssh user@server.com 'uptime && df -h'")
```
## Error Handling
- **Connection refused:** Check if SSH is running on the host (port 22).
- **Permission denied:** Key may not be authorized; use `ssh-copy-id`.
- **Host key verification:** Accept on first connect or check `~/.ssh/known_hosts`.

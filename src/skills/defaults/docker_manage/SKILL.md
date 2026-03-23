---
name: docker_manage
description: List, start, stop, and inspect Docker containers and images
openclaw:
  emoji: "🐳"
---
# Docker Management
Manage Docker containers and images.
## Steps
1. **List running containers:**
   ```bash
   run_shell_command("docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'")
   ```
2. **List all containers:**
   ```bash
   run_shell_command("docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'")
   ```
3. **Start/stop:**
   ```bash
   run_shell_command("docker start <container>")
   run_shell_command("docker stop <container>")
   ```
4. **View logs:**
   ```bash
   run_shell_command("docker logs --tail 50 <container>")
   ```
## Examples
### List containers
```bash
run_shell_command("docker ps")
```
## Error Handling
- **Docker not running:** `open -a Docker` on macOS.
- **Permission denied:** May need `sudo` or adding user to docker group.

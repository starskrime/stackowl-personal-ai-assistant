---
name: docker_manage
description: List, start, stop, and inspect Docker containers and images
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🐳"
parameters:
  action:
    type: string
    description: "Action: list, list_all, start, stop, logs, or status"
    default: "list"
  container:
    type: string
    description: "Container name or ID"
  image:
    type: string
    description: "Image name"
steps:
  - id: check_docker
    tool: ShellTool
    args:
      command: "docker info 2>/dev/null | head -5 || echo 'Docker not running'"
      mode: "local"
    timeout_ms: 10000
  - id: start_docker
    tool: ShellTool
    args:
      command: "open -a Docker 2>/dev/null || echo 'Please start Docker manually'"
      mode: "local"
    timeout_ms: 10000
  - id: list_containers
    tool: ShellTool
    args:
      command: "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"
      mode: "local"
    timeout_ms: 10000
  - id: list_all_containers
    tool: ShellTool
    args:
      command: "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'"
      mode: "local"
    timeout_ms: 10000
  - id: start_container
    tool: ShellTool
    args:
      command: "docker start {{container}}"
      mode: "local"
    timeout_ms: 30000
  - id: stop_container
    tool: ShellTool
    args:
      command: "docker stop {{container}}"
      mode: "local"
    timeout_ms: 30000
  - id: container_logs
    tool: ShellTool
    args:
      command: "docker logs --tail 50 {{container}}"
      mode: "local"
    timeout_ms: 30000
  - id: list_images
    tool: ShellTool
    args:
      command: "docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}'"
      mode: "local"
    timeout_ms: 10000
  - id: analyze
    type: llm
    prompt: "Docker action: '{{action}}'\n\nDocker status:\n{{check_docker.output}}\n\n{{#if_eq action 'list'}}Running containers:\n{{list_containers.output}}{{/if_eq}}\n{{#if_eq action 'list_all'}}All containers:\n{{list_all_containers.output}}{{/if_eq}}\n{{#if_eq action 'logs'}}Container logs for {{container}}:\n{{container_logs.output}}{{/if_eq}}"
    depends_on: [check_docker]
    inputs: [check_docker.output, list_containers.output, list_all_containers.output, container_logs.output]
---

# Docker Management

Manage Docker containers and images.

## Usage

List running containers:
```
/docker_manage
```

List all containers:
```
action=list_all
```

Start a container:
```
action=start
container=my_app
```

Stop a container:
```
action=stop
container=my_app
```

View logs:
```
action=logs
container=my_app
```

## Actions

- **list** (default): Show running containers
- **list_all**: Show all containers (running and stopped)
- **start**: Start a container
- **stop**: Stop a container
- **logs**: View container logs (last 50 lines)
- **status**: Check if Docker is running

## Examples

### List running containers
```
action=list
```

### Start a container
```
action=start
container=postgres_db
```

### View logs
```
action=logs
container=my_app
```

## Error Handling

- **Docker not running:** Attempts to start Docker on macOS
- **Permission denied:** May need sudo or docker group membership
- **Container not found:** Reports when container doesn't exist
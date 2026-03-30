---
name: port_scanner
description: Scan for open network ports on localhost or a specified host to check service availability
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔌"
parameters:
  host:
    type: string
    description: "Host to scan (default: localhost)"
    default: "localhost"
  ports:
    type: string
    description: "Ports to scan (e.g., 22,80,443 or 1-1000)"
    default: "22,80,443,3000,5432,6379,8080,8443,27017"
steps:
  - id: check_connectivity
    tool: ShellTool
    args:
      command: "ping -c 1 -W 1 {{host}} 2>/dev/null && echo 'reachable' || echo 'unreachable'"
      mode: "local"
    timeout_ms: 5000
  - id: scan_ports
    tool: ShellTool
    args:
      command: "for port in $(echo '{{ports}}' | tr ',' '\\n'); do (echo >/dev/tcp/{{host}}/$port) 2>/dev/null && echo \"Port $port: OPEN\" || echo \"Port $port: closed\"; done"
      mode: "local"
    timeout_ms: 60000
  - id: list_listening
    tool: ShellTool
    args:
      command: "lsof -i -P | grep LISTEN | head -20"
      mode: "local"
    timeout_ms: 10000
  - id: analyze
    type: llm
    prompt: "Port scan results for: {{host}}\n\nHost status: {{check_connectivity.output}}\n\nOpen ports:\n{{scan_ports.output}}\n\nListening services:\n{{list_listening.output}}"
    depends_on: [check_connectivity]
    inputs: [scan_ports.output, list_listening.output]
---

# Port Scanner

Scan for open network ports on a host.

## Usage

```bash
/port_scanner
```

Scan specific host:
```
host=192.168.1.1
```

Scan custom ports:
```
host=localhost
ports=80,443,8080,3000
```

## Parameters

- **host**: Target host (default: localhost)
- **ports**: Comma-separated port list

## Common Ports

- 22: SSH
- 80: HTTP
- 443: HTTPS
- 3000: Development servers
- 5432: PostgreSQL
- 6379: Redis
- 8080: HTTP alternate
- 8443: HTTPS alternate
- 27017: MongoDB

## Examples

### Scan localhost
```
host=localhost
```

### Check web ports
```
host=example.com
ports=80,443,8080
```

### Full scan
```
host=my-server
ports=1-1000
```

## Error Handling

- **Host unreachable**: Check network first
- **Permission denied**: Some scans need sudo
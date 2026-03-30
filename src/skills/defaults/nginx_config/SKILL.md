---
name: nginx_config
description: Generate Nginx configuration files for reverse proxy, static sites, or SSL termination
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "⚡"
parameters:
  domain:
    type: string
    description: "The domain name for the nginx config"
    default: "example.com"
  port:
    type: number
    description: "The port number to proxy to"
    default: 3000
  config_type:
    type: string
    description: "Type of config: reverse_proxy, static_site, ssl"
    default: "reverse_proxy"
required: [domain]
steps:
  - id: validate_tools
    tool: ShellTool
    args:
      command: "which nginx && nginx -v"
      mode: "local"
  - id: check_port
    tool: ShellTool
    args:
      command: "lsof -i :{{port}} 2>/dev/null || echo 'Port {{port}} is available'"
      mode: "local"
  - id: generate_config
    type: llm
    prompt: "Generate an nginx config for a {{config_type}} for domain {{domain}} proxying to localhost:{{port}}. Output ONLY the nginx config block without markdown formatting."
    depends_on: [check_port]
    inputs: [domain, port, config_type]
  - id: save_config
    tool: ShellTool
    args:
      command: "echo 'server {\n    listen 80;\n    server_name {{domain}};\n    location / {\n        proxy_pass http://localhost:{{port}};\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n    }\n}' | sudo tee /etc/nginx/sites-available/{{domain}}"
      mode: "local"
    optional: true
  - id: validate_config
    tool: ShellTool
    args:
      command: "sudo nginx -t"
      mode: "local"
---

# Nginx Config Generator

Generate Nginx configurations.

## Usage

```bash
/nginx_config domain=app.example.com port=3000 config_type=reverse_proxy
```

## Parameters

- **domain**: The domain name for the nginx config (required)
- **port**: The port number to proxy to (default: 3000)
- **config_type**: Type of config: reverse_proxy, static_site, ssl (default: reverse_proxy)

## Examples

### Reverse proxy config

```nginx
server {
    listen 80;
    server_name app.example.com;
    location / {
        proxy_pass http://localhost:3000;
    }
}
```

## Error Handling

- **Syntax error:** `nginx -t` will report the issue.
- **Port conflict:** Check with `lsof -i :<port>`.

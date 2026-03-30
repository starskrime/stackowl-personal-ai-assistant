---
name: network_check
description: Quick network health check — tests connectivity, DNS, and reports public IP
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔍"
  requires:
    bins: ["ping", "curl"]
  os: ["darwin", "linux"]
parameters:
  domain:
    type: string
    description: Domain to test connectivity against
    default: "google.com"
  count:
    type: number
    description: Number of ping packets
    default: 3
required: []
steps:
  - id: ping
    tool: ShellTool
    args:
      command: "ping -c {{count}} {{domain}} 2>&1 | tail -3"
    timeout_ms: 15000
    on_failure: ping_fallback
  - id: ping_fallback
    tool: ShellTool
    args:
      command: "ping -c 1 8.8.8.8 2>&1 | tail -3"
    timeout_ms: 10000
    optional: true
  - id: dns
    tool: ShellTool
    args:
      command: "nslookup {{domain}} 2>&1 | head -10"
    timeout_ms: 10000
  - id: public_ip
    tool: ShellTool
    args:
      command: "curl -s --max-time 5 ifconfig.me"
    timeout_ms: 10000
    optional: true
  - id: analyze
    type: llm
    prompt: "Analyze these network test results for {{domain}} and give a clear health report. Is the network working? Any issues?"
    depends_on: [ping, dns, public_ip]
    inputs: [ping.output, dns.output, public_ip.output]
---

# Network Check (Structured)

Automated network health check using structured execution.
Steps run in parallel where possible and results are analyzed by the LLM.

## Usage

```bash
/network_check domain=<domain> count=<count>
```

## Parameters

- **domain**: Domain to test connectivity against (default: google.com)
- **count**: Number of ping packets (default: 3)

## Examples

### Check connectivity to Google

```
domain=google.com
count=3
```

### Check a custom domain

```
domain=example.com
count=5
```

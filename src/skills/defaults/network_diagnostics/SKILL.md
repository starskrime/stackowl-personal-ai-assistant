---
name: network_diagnostics
description: Diagnose network connectivity issues by testing DNS, ping, traceroute, and internet speed
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🌐"
  os: [darwin, linux]
parameters:
  destination:
    type: string
    description: Target host or domain to diagnose
    default: "google.com"
  trace_hops:
    type: number
    description: Maximum number of traceroute hops
    default: 15
steps:
  - id: ping_test
    tool: ShellTool
    args:
      command: "ping -c 3 8.8.8.8 2>&1 | tail -5"
      mode: "local"
    timeout_ms: 15000
    on_failure: ping_failed
  - id: ping_failed
    tool: ShellTool
    args:
      command: "echo 'Ping to 8.8.8.8 failed'"
      mode: "local"
    optional: true
  - id: dns_test
    tool: ShellTool
    args:
      command: "nslookup {{destination}} 2>&1 | head -15"
      mode: "local"
    timeout_ms: 10000
  - id: trace_route
    tool: ShellTool
    args:
      command: "traceroute -m {{trace_hops}} {{destination}} 2>&1 | head -20"
      mode: "local"
    timeout_ms: 20000
    optional: true
  - id: local_ip
    tool: ShellTool
    args:
      command: "ifconfig en0 2>/dev/null | grep 'inet ' || ip addr show 2>/dev/null | grep 'inet '"
      mode: "local"
    timeout_ms: 5000
  - id: public_ip
    tool: ShellTool
    args:
      command: "curl -s --max-time 5 ifconfig.me 2>/dev/null || echo 'unavailable'"
      mode: "local"
    timeout_ms: 10000
    optional: true
  - id: analyze
    type: llm
    prompt: "You are a network diagnostics assistant. Analyze the following test results and provide a clear diagnosis:\n\n- Ping to 8.8.8.8: {{ping_test.output}}\n- DNS lookup for {{destination}}: {{dns_test.output}}\n- Traceroute: {{trace_route.output}}\n- Local IP: {{local_ip.output}}\n- Public IP: {{public_ip.output}}\n\nIs there a network connectivity issue? What are the likely causes?"
    depends_on: [ping_test, dns_test, local_ip]
    inputs: [ping_test.output, dns_test.output, trace_route.output, local_ip.output, public_ip.output]
---

# Network Diagnostics

Troubleshoot network connectivity issues with comprehensive tests.

## Usage

Run with default settings (tests google.com):
```
/network_diagnostics
```

Run with custom destination:
```
/network_diagnostics google.com
```

## What it checks

1. **Internet connectivity** — Ping to 8.8.8.8 (Google DNS)
2. **DNS resolution** — Lookup for the target domain
3. **Route trace** — Path packets take to reach destination
4. **Local IP** — Your machine's IP address
5. **Public IP** — Your external IP address

## Examples

### Quick check
```
destination=8.8.8.8
```

### Full trace with custom hops
```
destination=example.com
trace_hops=30
```

## Error Handling

- **Ping fails:** Network is down — check WiFi/ethernet connection
- **DNS fails but ping works:** DNS issue — try 8.8.8.8 or 1.1.1.1 as DNS
- **Traceroute times out:** Network congestion or firewall blocking

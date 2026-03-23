---
name: network_check
description: Quick network health check — tests connectivity, DNS, and reports public IP
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
steps:
  - id: ping
    tool: run_shell_command
    args:
      command: "ping -c {{count}} {{domain}} 2>&1 | tail -3"
    timeout_ms: 15000
    on_failure: ping_fallback
  - id: ping_fallback
    tool: run_shell_command
    args:
      command: "ping -c 1 8.8.8.8 2>&1 | tail -3"
    timeout_ms: 10000
    optional: true
  - id: dns
    tool: run_shell_command
    args:
      command: "nslookup {{domain}} 2>&1 | head -10"
    timeout_ms: 10000
  - id: public_ip
    tool: run_shell_command
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

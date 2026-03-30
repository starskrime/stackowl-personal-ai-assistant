---
name: server_monitor
description: Monitor server health by checking HTTP endpoints, response times, and status codes
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📡"
parameters:
  url:
    type: string
    description: "URL to check"
  timeout:
    type: number
    description: "Request timeout in seconds"
    default: 10
steps:
  - id: check_endpoint
    tool: ShellTool
    args:
      command: "curl -s -o /dev/null -w 'HTTP_CODE:%{http_code}\\nTIME_TOTAL:%{time_total}s\\nSIZE_DOWNLOAD:%{size_download}b\\n' --max-time {{timeout}} '{{url}}'"
      mode: "local"
    timeout_ms: 30000
  - id: get_headers
    tool: ShellTool
    args:
      command: "curl -s -I --max-time {{timeout}} '{{url}}' | head -15"
      mode: "local"
    timeout_ms: 15000
  - id: measure_latency
    tool: ShellTool
    args:
      command: "curl -s -o /dev/null -w 'DNS:%{time_namelookup}s CONNECT:%{time_connect}s TTFB:%{time_starttransfer}s TOTAL:%{time_total}s' --max-time {{timeout}} '{{url}}'"
      mode: "local"
    timeout_ms: 30000
  - id: analyze
    type: llm
    prompt: "Server health check for: {{url}}\n\nStatus:\n{{check_endpoint.output}}\n\nHeaders:\n{{get_headers.output}}\n\nLatency breakdown:\n{{measure_latency.output}}\n\nProvide a health summary."
    depends_on: [check_endpoint]
    inputs: [check_endpoint.output, get_headers.output, measure_latency.output]
---

# Server Monitor

Check server/endpoint health and performance.

## Usage

```bash
/server_monitor https://api.example.com/health
```

## Parameters

- **url**: Full URL to check
- **timeout**: Request timeout in seconds (default: 10)

## Output

- **HTTP status code**: 200 = healthy
- **Response time**: Total time in seconds
- **Response size**: Downloaded bytes
- **Headers**: Server headers
- **Latency breakdown**: DNS, connect, TTFB, total

## Examples

### Check API health
```
url=https://api.example.com/health
```

### Check with longer timeout
```
url=https://slow.example.com
timeout=30
```

## Status Codes

- **2xx**: Success
- **3xx**: Redirect (may need follow-up)
- **4xx**: Client error
- **5xx**: Server error
- **0**: Connection failed
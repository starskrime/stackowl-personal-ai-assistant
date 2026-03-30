---
name: api_test
description: Test REST API endpoints by sending HTTP requests and validating response status, headers, and body
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔌"
parameters:
  url:
    type: string
    description: "The URL to test"
  method:
    type: string
    description: "HTTP method (GET, POST, PUT, DELETE, PATCH)"
    default: "GET"
  body:
    type: string
    description: "Request body for POST/PUT/PATCH (JSON string)"
    default: ""
  headers:
    type: string
    description: "Additional headers as JSON string"
    default: "{}"
required: [url]
steps:
  - id: construct_request
    tool: ShellTool
    args:
      command: "curl -s -w '\n%{http_code}' -X {{method}} '{{url}}' -H 'Content-Type: application/json' {{#if headers}}-H {{headers}}{{/if}} {{#if body}}-d '{{body}}'{{/if}}"
      mode: "local"
    timeout_ms: 30000
  - id: parse_response
    type: llm
    prompt: "Parse the API response. Extract: 1) HTTP status code, 2) Response body (format as JSON if possible), 3) Any errors. Present in a clear format."
    depends_on: [construct_request]
    inputs: [construct_request.stdout]
---

# API Test

Test API endpoints using curl.

## Steps

1. **Construct the request:**
   ```bash
   curl -s -w '\n%{http_code}' -X <METHOD> '<URL>' -H 'Content-Type: application/json' -d '<body>'
   ```
2. **Validate the response:**
   - Check HTTP status code (200, 201, 4xx, 5xx)
   - Parse JSON response body
   - Verify expected fields exist
3. **Present results** with status, headers, and formatted body.

## Examples

### GET request

```bash
url="https://api.example.com/users"
```

### POST with JSON body

```bash
url="https://api.example.com/users"
method="POST"
body='{"name":"John"}'
```

## Error Handling

- **Connection refused:** Check if server is running. Suggest `curl -v` for debug info.
- **SSL error:** Try with `--insecure` flag for self-signed certs.
- **Timeout:** Add `--max-time 10` flag.

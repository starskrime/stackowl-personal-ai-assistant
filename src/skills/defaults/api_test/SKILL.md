---
name: api_test
description: Test REST API endpoints by sending HTTP requests and validating response status, headers, and body
openclaw:
  emoji: "🔌"
---
# API Test
Test API endpoints using curl.
## Steps
1. **Construct the request:**
   ```bash
   run_shell_command("curl -s -w '\n%{http_code}' -X <METHOD> '<URL>' -H 'Content-Type: application/json' -d '<body>'")
   ```
2. **Validate the response:**
   - Check HTTP status code (200, 201, 4xx, 5xx)
   - Parse JSON response body
   - Verify expected fields exist
3. **Present results** with status, headers, and formatted body.
## Examples
### GET request
```bash
run_shell_command("curl -s -w '\n%{http_code}' https://api.example.com/users")
```
### POST with JSON body
```bash
run_shell_command("curl -s -X POST 'https://api.example.com/users' -H 'Content-Type: application/json' -d '{\"name\":\"John\"}'")
```
## Error Handling
- **Connection refused:** Check if server is running. Suggest `curl -v` for debug info.
- **SSL error:** Try with `--insecure` flag for self-signed certs.
- **Timeout:** Add `--max-time 10` flag.

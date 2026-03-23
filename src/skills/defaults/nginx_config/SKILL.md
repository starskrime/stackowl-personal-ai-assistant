---
name: nginx_config
description: Generate Nginx configuration files for reverse proxy, static sites, or SSL termination
openclaw:
  emoji: "⚡"
---
# Nginx Config Generator
Generate Nginx configurations.
## Steps
1. **Determine use case:** reverse proxy, static site, SSL, load balancer.
2. **Generate config:**
   ```nginx
   server {
       listen 80;
       server_name <domain>;
       location / {
           proxy_pass http://localhost:<port>;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
       }
   }
   ```
3. **Save and validate:**
   ```bash
   write_file("/etc/nginx/sites-available/<domain>", "<config>")
   run_shell_command("nginx -t")
   ```
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

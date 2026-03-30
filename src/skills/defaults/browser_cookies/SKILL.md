---
name: browser_cookies
description: View, export, import, and delete browser cookies for Safari and Chrome
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🍪"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: list, export, import, delete, or clear"
    default: "list"
  browser:
    type: string
    description: "Browser: safari or chrome"
    default: "safari"
  domain:
    type: string
    description: "Domain filter (e.g., github.com)"
  output_file:
    type: string
    description: "Output file for export (JSON/Netscape format)"
    default: "~/Desktop/cookies_export.json"
required: []
steps:
  - id: list_safari_cookies
    tool: ShellTool
    args:
      command: "sqlite3 ~/Library/Cookies/Cookies.binarycookies 'SELECT * FROM cookies LIMIT 20' 2>/dev/null | head -20 || echo 'Could not read Safari cookies directly'"
      mode: "local"
    timeout_ms: 10000
  - id: list_chrome_cookies
    tool: ShellTool
    args:
      command: "sqlite3 ~/Library/Application\ Support/Google/Chrome/Default/Cookies 'SELECT host_key, name, value FROM cookies WHERE host_key LIKE \"%{{domain}}%\" LIMIT 20' 2>/dev/null"
      mode: "local"
    timeout_ms: 10000
  - id: export_chrome_cookies
    tool: ShellTool
    args:
      command: "sqlite3 ~/Library/Application\ Support/Google/Chrome/Default/Cookies 'SELECT host_key, name, value, path, expires_utc FROM cookies' 2>/dev/null > '{{output_file}}' && echo 'Cookies exported to {{output_file}}'"
      mode: "local"
    timeout_ms: 15000
  - id: export_netscape_format
    tool: ShellTool
    args:
      command: "echo '# Netscape HTTP Cookie File' > '{{output_file}}' && sqlite3 ~/Library/Application\ Support/Google/Chrome/Default/Cookies 'SELECT host_key, \"FALSE\", path, \"FALSE\", expires_utc, name, value FROM cookies' 2>/dev/null >> '{{output_file}}' && echo 'Exported in Netscape format'"
      mode: "local"
    timeout_ms: 15000
  - id: clear_chrome_cookies
    tool: ShellTool
    args:
      command: "sqlite3 ~/Library/Application\ Support/Google/Chrome/Default/Cookies 'DELETE FROM cookies WHERE host_key LIKE \"%{{domain}}%\"' 2>/dev/null && echo 'Cookies for {{domain}} cleared'"
      mode: "local"
    timeout_ms: 10000
  - id: clear_all_chrome_cookies
    tool: ShellTool
    args:
      command: "sqlite3 ~/Library/Application\ Support/Google/Chrome/Default/Cookies 'DELETE FROM cookies' 2>/dev/null && echo 'All Chrome cookies cleared'"
      mode: "local"
    timeout_ms: 15000
  - id: cookie_count
    tool: ShellTool
    args:
      command: "sqlite3 ~/Library/Application\ Support/Google/Chrome/Default/Cookies 'SELECT COUNT(*) FROM cookies' 2>/dev/null || echo '0'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Browser cookies - {{browser}}\n\nCookie count: {{cookie_count}}\n\n{{#if_eq action 'list'}}Cookies:\n{{list_chrome_cookies.output}}{{/if_eq}}"
    depends_on: [cookie_count]
    inputs: [cookie_count.output, list_chrome_cookies.output]
---

# Browser Cookies

View and manage browser cookies.

## Usage

List cookies for domain:
```
action=list
browser=chrome
domain=github.com
```

Export cookies:
```
action=export
browser=chrome
output_file=~/Desktop/cookies.json
```

Clear cookies for domain:
```
action=delete
browser=chrome
domain=github.com
```

Clear all cookies:
```
action=clear
browser=chrome
```

## Actions

- **list**: Show cookies for domain
- **export**: Export cookies to JSON or Netscape format
- **import**: Import cookies from file
- **delete**: Delete cookies for domain
- **clear**: Clear all cookies

## Examples

### Export GitHub cookies
```
action=export
browser=chrome
domain=github.com
output_file=~/Desktop/github_cookies.json
```

### List GitHub cookies
```
action=list
browser=chrome
domain=github.com
```

## Notes

- Safari cookies are in binary format (harder to read)
- Chrome cookies easily readable via SQLite
- Close Chrome before deleting cookies
- Netscape format for curl/wget cookie import
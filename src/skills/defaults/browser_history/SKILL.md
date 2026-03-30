---
name: browser_history
description: View, search, and manage browser history across Safari, Chrome, and Firefox on macOS
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📜"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: view, search, or clear"
    default: "view"
  browser:
    type: string
    description: "Browser: safari, chrome, firefox, or all"
    default: "all"
  query:
    type: string
    description: "Search query for history"
  days:
    type: number
    description: "How many days back to show"
    default: 7
required: []
steps:
  - id: safari_history
    tool: ShellTool
    args:
      command: "sqlite3 ~/Library/Safari/WebpageIcons.db 'SELECT * FROM page_url WHERE url LIKE \"%{{query}}%\" LIMIT 20' 2>/dev/null || echo 'No Safari history found'"
      mode: "local"
    timeout_ms: 15000
  - id: chrome_history
    tool: ShellTool
    args:
      command: "sqlite3 ~/Library/Application\ Support/Google/Chrome/Default/History 'SELECT url, title, visit_count FROM urls WHERE url LIKE \"%{{query}}%\" ORDER BY last_visit_time DESC LIMIT 20' 2>/dev/null || echo 'No Chrome history found'"
      mode: "local"
    timeout_ms: 15000
  - id: firefox_history
    tool: ShellTool
    args:
      command: "sqlite3 ~/Library/Application\ Support/Firefox/Profiles/*/places.sqlite 'SELECT url, title FROM moz_places WHERE url LIKE \"%{{query}}%\" ORDER BY last_visit_date DESC LIMIT 20' 2>/dev/null || echo 'No Firefox history found'"
      mode: "local"
    timeout_ms: 15000
  - id: safari_history_recent
    tool: ShellTool
    args:
      command: "defaults read com.apple.Safari RecentWebpages | grep -E 'URL|Name' | tail -30"
      mode: "local"
    timeout_ms: 10000
  - id: clear_safari
    tool: ShellTool
    args:
      command: "rm -f ~/Library/Safari/WebpageIcons.db && echo 'Safari history cleared'"
      mode: "local"
    timeout_ms: 10000
  - id: clear_chrome
    tool: ShellTool
    args:
      command: "rm -f ~/Library/Application\ Support/Google/Chrome/Default/History-journal && echo 'Chrome history cleared (restart Chrome to complete)'"
      mode: "local"
    timeout_ms: 10000
  - id: list_browsers
    tool: ShellTool
    args:
      command: "ls /Applications | grep -iE 'safari|chrome|firefox|edge' | sort"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Browser history for: '{{browser}}' searching '{{query}}'\n\nInstalled browsers:\n{{list_browsers.output}}\n\n{{#if_eq browser 'safari'}}Safari history:\n{{safari_history.output}}{{/if_eq}}\n{{#if_eq browser 'chrome'}}Chrome history:\n{{chrome_history.output}}{{/if_eq}}\n{{#if_eq browser 'firefox'}}Firefox history:\n{{firefox_history.output}}{{/if_eq}}\n{{#if_eq browser 'all'}}All history:\nSafari: {{safari_history.output}}\nChrome: {{chrome_history.output}}\nFirefox: {{firefox_history.output}}{{/if_eq}}"
    depends_on: [list_browsers]
    inputs: [safari_history.output, chrome_history.output, firefox_history.output]
---

# Browser History

View, search, and manage browsing history.

## Usage

View recent history:
```
/browser_history
```

Search for a site:
```
action=search
query=github
browser=chrome
```

Clear history:
```
action=clear
browser=safari
```

## Actions

- **view**: Show recent browsing history
- **search**: Search history for specific query
- **clear**: Clear browsing history

## Browsers

- **safari**: macOS Safari
- **chrome**: Google Chrome
- **firefox**: Mozilla Firefox
- **all**: Check all browsers

## Examples

### View Safari history
```
action=view
browser=safari
days=7
```

### Search Chrome history
```
action=search
browser=chrome
query=stackoverflow
```

### Clear Firefox history
```
action=clear
browser=firefox
```

## Notes

- History files may be locked if browser is open
- Close browser before clearing for complete removal
- Firefox uses different profile structure
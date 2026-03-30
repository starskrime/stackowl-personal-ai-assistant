---
name: browser_bookmarks
description: List, search, add, and manage browser bookmarks for Safari and Chrome on macOS
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔖"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: list, search, add, or export"
    default: "list"
  browser:
    type: string
    description: "Browser: safari or chrome"
    default: "safari"
  query:
    type: string
    description: "Search term for bookmarks"
  title:
    type: string
    description: "Bookmark title (for add action)"
  url:
    type: string
    description: "Bookmark URL (for add action)"
  folder:
    type: string
    description: "Folder name for bookmark"
    default: "BookmarksBar"
required: []
steps:
  - id: list_safari_bookmarks
    tool: ShellTool
    args:
      command: "defaults read com.apple.Safari BookmarksBar | grep -E 'URL|string' | head -30"
      mode: "local"
    timeout_ms: 10000
  - id: safari_bookmarks_plist
    tool: ShellTool
    args:
      command: "plutil -convert xml1 -o - ~/Library/Safari/Bookmarks.plist 2>/dev/null | grep -A2 -E '<string>http' | head -60"
      mode: "local"
    timeout_ms: 10000
  - id: list_chrome_bookmarks
    tool: ShellTool
    args:
      command: "cat ~/Library/Application\ Support/Google/Chrome/Default/Bookmarks 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); [print(b['name'],':',b['url']) for b in d.get('roots',{}).get('bookmark_bar',{}).get('children',[]) if 'url' in b]\" 2>/dev/null | head -30 || echo 'No Chrome bookmarks found'"
      mode: "local"
    timeout_ms: 10000
  - id: search_bookmarks
    tool: ShellTool
    args:
      command: "grep -ri '{{query}}' ~/Library/Safari/Bookmarks.plist ~/Library/Application\ Support/Google/Chrome/Default/Bookmarks 2>/dev/null | head -20"
      mode: "local"
    timeout_ms: 15000
  - id: add_safari_bookmark
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Safari\" to add bookmark at end of bookmarks of document 1' 2>/dev/null || echo 'Use File > Add Bookmark in Safari'"
      mode: "local"
    timeout_ms: 10000
  - id: export_bookmarks
    tool: ShellTool
    args:
      command: "echo '=== Safari Bookmarks ===' > ~/Desktop/bookmarks_export.html && plutil -convert xml1 -o - ~/Library/Safari/Bookmarks.plist 2>/dev/null >> ~/Desktop/bookmarks_export.html && echo 'Exported to ~/Desktop/bookmarks_export.html'"
      mode: "local"
    timeout_ms: 15000
  - id: analyze
    type: llm
    prompt: "Browser bookmarks - {{browser}}\n\n{{#if_eq action 'list'}}Safari:\n{{safari_bookmarks_plist.output}}\n\nChrome:\n{{list_chrome_bookmarks.output}}{{/if_eq}}\n{{#if_eq action 'search'}}Search results:\n{{search_bookmarks.output}}{{/if_eq}}"
    depends_on: [safari_bookmarks_plist]
    inputs: [safari_bookmarks_plist.output, list_chrome_bookmarks.output]
---

# Browser Bookmarks

List, search, and manage browser bookmarks.

## Usage

List bookmarks:
```
/browser_bookmarks
```

Search bookmarks:
```
action=search
query=github
browser=safari
```

Export all bookmarks:
```
action=export
browser=safari
```

## Actions

- **list**: Show all bookmarks
- **search**: Search bookmarks by keyword
- **add**: Add current page as bookmark
- **export**: Export bookmarks to HTML file

## Browsers

- **safari** (default): macOS Safari
- **chrome**: Google Chrome

## Examples

### List Safari bookmarks
```
action=list
browser=safari
```

### Search Chrome
```
action=search
browser=chrome
query=work
```

### Export to HTML
```
action=export
browser=safari
```

## Notes

- Chrome must be closed to read bookmarks file
- Safari bookmarks stored in property list
- Export creates HTML file on Desktop
---
name: browser_extensions
description: List, enable, disable, and manage browser extensions and add-ons for Chrome and Firefox
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🧩"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: list, enable, disable, or uninstall"
    default: "list"
  browser:
    type: string
    description: "Browser: chrome or firefox"
    default: "chrome"
  extension_id:
    type: string
    description: "Extension ID for targeted operations"
required: []
steps:
  - id: list_chrome_extensions
    tool: ShellTool
    args:
      command: "ls ~/Library/Application\ Support/Google/Chrome/Default/Extensions/ 2>/dev/null | head -30"
      mode: "local"
    timeout_ms: 10000
  - id: chrome_extension_details
    tool: ShellTool
    args:
      command: "for ext in ~/Library/Application\ Support/Google/Chrome/Default/Extensions/*/; do echo \"=== $(basename $ext) ===\"; ls \"$ext\" 2>/dev/null | head -1; done | head -50"
      mode: "local"
    timeout_ms: 15000
  - id: list_firefox_addons
    tool: ShellTool
    args:
      command: "ls ~/Library/Application\ Support/Firefox/Profiles/*/extensions/ 2>/dev/null | head -30 || echo 'No Firefox extensions found'"
      mode: "local"
    timeout_ms: 10000
  - id: enable_chrome_extension
    tool: ShellTool
    args:
      command: "open 'chrome://extensions/?id={{extension_id}}' && echo 'Open Chrome to enable extension'"
      mode: "local"
    timeout_ms: 5000
  - id: disable_chrome_extension
    tool: ShellTool
    args:
      command: "rm -rf ~/Library/Application\ Support/Google/Chrome/Default/Extensions/{{extension_id}} && echo 'Extension disabled (folder removed)'"
      mode: "local"
    timeout_ms: 10000
  - id: extension_count
    tool: ShellTool
    args:
      command: "ls ~/Library/Application\ Support/Google/Chrome/Default/Extensions/ 2>/dev/null | wc -l | tr -d ' '"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Browser extensions for {{browser}}:\n\nExtension count: {{extension_count}}\n\n{{#if_eq browser 'chrome'}}Extensions:\n{{chrome_extension_details.output}}{{/if_eq}}\n{{#if_eq browser 'firefox'}}Add-ons:\n{{list_firefox_addons.output}}{{/if_eq}}"
    depends_on: [extension_count]
    inputs: [extension_count.output, chrome_extension_details.output]
---

# Browser Extensions

List and manage browser extensions.

## Usage

List all extensions:
```
/browser_extensions
```

Enable/disable extension:
```
action=enable
browser=chrome
extension_id=extension_id_here
```

## Actions

- **list**: Show all installed extensions
- **enable**: Enable extension (opens Chrome)
- **disable**: Disable by removing extension folder
- **uninstall**: Remove extension completely

## Examples

### List Chrome extensions
```
action=list
browser=chrome
```

### Get extension details
```
action=list
browser=chrome
```

## Notes

- Firefox uses profiles with different extension locations
- Chrome extensions stored in ~/Library
- Disable by removing extension folder
- Full uninstall requires removing all data
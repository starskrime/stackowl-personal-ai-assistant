---
name: browser_proxy
description: Configure, enable, disable, and manage system proxy settings and PAC files on macOS
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔄"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: status, enable, disable, set, or auto"
    default: "status"
  proxy_server:
    type: string
    description: "Proxy server address (e.g., localhost:8080)"
  proxy_type:
    type: string
    description: "Type: http, https, socks5"
    default: "http"
  pac_url:
    type: string
    description: "PAC file URL for auto-configuration"
required: []
steps:
  - id: get_proxy_status
    tool: ShellTool
    args:
      command: "networksetup -getwebproxy Wi-Fi && networksetup -getsecurewebproxy Wi-Fi"
      mode: "local"
    timeout_ms: 10000
  - id: get_proxy_status_alt
    tool: ShellTool
    args:
      command: "scutil --proxy 2>/dev/null | head -20"
      mode: "local"
    timeout_ms: 5000
  - id: enable_http_proxy
    tool: ShellTool
    args:
      command: "networksetup -setwebproxy Wi-Fi {{proxy_server}} && echo 'HTTP proxy enabled'"
      mode: "local"
    timeout_ms: 5000
  - id: disable_http_proxy
    tool: ShellTool
    args:
      command: "networksetup -setwebproxystate Wi-Fi off && echo 'HTTP proxy disabled'"
      mode: "local"
    timeout_ms: 5000
  - id: enable_https_proxy
    tool: ShellTool
    args:
      command: "networksetup -setsecurewebproxy Wi-Fi {{proxy_server}} && echo 'HTTPS proxy enabled'"
      mode: "local"
    timeout_ms: 5000
  - id: disable_https_proxy
    tool: ShellTool
    args:
      command: "networksetup -setsecurewebproxystate Wi-Fi off && echo 'HTTPS proxy disabled'"
      mode: "local"
    timeout_ms: 5000
  - id: set_pac
    tool: ShellTool
    args:
      command: "networksetup -setautoproxyurl Wi-Fi '{{pac_url}}' && echo 'PAC URL set'"
      mode: "local"
    timeout_ms: 5000
  - id: disable_pac
    tool: ShellTool
    args:
      command: "networksetup -setautoproxystate Wi-Fi off && echo 'PAC disabled'"
      mode: "local"
    timeout_ms: 5000
  - id: clear_all_proxy
    tool: ShellTool
    args:
      command: "networksetup -setwebproxystate Wi-Fi off && networksetup -setsecurewebproxystate Wi-Fi off && networksetup -setautoproxystate Wi-Fi off && echo 'All proxies disabled'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Proxy status:\n\n{{get_proxy_status.output}}\n\n{{get_proxy_status_alt.output}}"
    depends_on: [get_proxy_status]
    inputs: [get_proxy_status.output]
---

# Browser Proxy

Configure system proxy settings on macOS.

## Usage

Check proxy status:
```
/browser_proxy
```

Enable HTTP proxy:
```
action=enable
proxy_server=localhost:8080
```

Disable all proxies:
```
action=disable
```

Set PAC file:
```
action=auto
pac_url=http://proxy.example.com/pac
```

## Actions

- **status**: Show current proxy configuration
- **enable**: Enable HTTP or HTTPS proxy
- **disable**: Disable proxy by type
- **set**: Set proxy server
- **auto**: Configure PAC (auto-discover)
- **clear**: Disable all proxies

## Parameters

- **proxy_server**: Server:port (e.g., 192.168.1.1:8080)
- **proxy_type**: http, https, or socks5
- **pac_url**: URL to PAC file

## Examples

### Enable HTTP proxy
```
action=enable
proxy_server=localhost:8888
```

### Disable all
```
action=clear
```

### Set PAC
```
action=auto
pac_url=http://config.proxy/pac
```

## Notes

- Changes apply to entire system
- May require admin password
- Wi-Fi interface shown - adjust if needed
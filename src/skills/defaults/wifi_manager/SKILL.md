---
name: wifi_manager
description: View current WiFi connection, scan available networks, and connect to a specified network on macOS
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📶"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: status, scan, connect, or disconnect"
    default: "status"
  ssid:
    type: string
    description: "WiFi network name"
  password:
    type: string
    description: "WiFi password (for connect action)"
steps:
  - id: wifi_status
    tool: ShellTool
    args:
      command: "networksetup -getairportnetwork en0"
      mode: "local"
    timeout_ms: 5000
  - id: scan_networks
    tool: ShellTool
    args:
      command: "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport -s"
      mode: "local"
    timeout_ms: 10000
  - id: connect_wifi
    tool: ShellTool
    args:
      command: "networksetup -setairportnetwork en0 '{{ssid}}' '{{password}}'"
      mode: "local"
    timeout_ms: 30000
  - id: disconnect_wifi
    tool: ShellTool
    args:
      command: "networksetup -setairportpower en0 off && networksetup -setairportpower en0 on"
      mode: "local"
    timeout_ms: 10000
  - id: signal_strength
    tool: ShellTool
    args:
      command: "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport -I | grep 'agrCtlRSSI'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "WiFi manager - action: '{{action}}'\n\nCurrent network: {{wifi_status.output}}\n\nSignal: {{signal_strength.output}}\n\n{{#if_eq action 'scan'}}Available networks:\n{{scan_networks.output}}{{/if_eq}}\n{{#if_eq action 'connect'}}Attempting to connect to: {{ssid}}{{/if_eq}}"
    depends_on: [wifi_status]
    inputs: [wifi_status.output, scan_networks.output, signal_strength.output]
---

# WiFi Manager

Manage WiFi connections on macOS.

## Usage

Check WiFi status:
```
/wifi_manager
```

Scan for networks:
```
action=scan
```

Connect to a network:
```
action=connect
ssid=MyNetwork
password=secret
```

Disconnect:
```
action=disconnect
```

## Actions

- **status** (default): Show current WiFi connection
- **scan**: List available networks
- **connect**: Connect to a specific network
- **disconnect**: Disconnect from current network

## Examples

### Check status
```
action=status
```

### Scan networks
```
action=scan
```

### Connect to WiFi
```
action=connect
ssid=HomeNetwork
password=mypassword
```

## Notes

- Requires password for protected networks
- WiFi must be enabled
- Interface is typically en0 on macOS
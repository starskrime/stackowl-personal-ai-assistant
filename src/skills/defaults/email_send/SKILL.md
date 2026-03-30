---
name: email_send
description: Send emails via macOS Mail app using AppleScript with support for recipients, subject, body, and attachments
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "✉️"
  os: [darwin]
parameters:
  to:
    type: string
    description: "Recipient email address"
  subject:
    type: string
    description: "Email subject"
    default: "(No Subject)"
  body:
    type: string
    description: "Email body text"
  attachment:
    type: string
    description: "File path to attach"
  cc:
    type: string
    description: "CC recipients (comma-separated)"
  bcc:
    type: string
    description: "BCC recipients (comma-separated)"
required: [to]
steps:
  - id: check_mail
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Mail\" to return name of application \"Mail\"' 2>/dev/null || echo 'Mail not configured'"
      mode: "local"
    timeout_ms: 5000
  - id: send_basic
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Mail\"\n set newMessage to make new outgoing message with properties {subject:\"{{subject}}\", content:\"{{body}}\", visible:false}\n tell newMessage\n make new to recipient at end of to recipients with properties {address:\"{{to}}\"}\n{{#if cc}} make new cc recipient at end of cc recipients with properties {address:\"{{cc}}\"}{{cc}}{{/if}}\n{{#if attachment}} set theAttachment to POSIX file \"{{attachment}}\"\n make new attachment at end of attachments with properties {file name:theAttachment}\n{{/if}}\n send\n end tell\nend tell' && echo 'Email sent to {{to}}'"
      mode: "local"
    timeout_ms: 30000
  - id: send_with_cc
    tool: ShellTool
    args:
      command: "osascript -e 'set recipients to \"{{to}},{{cc}}\"\nset theSubject to \"{{subject}}\"\nset theBody to \"{{body}}\"\n\ntell application \"Mail\"\n set newMessage to make new outgoing message with properties {subject:theSubject, content:theBody, visible:false}\n \n set AppleScript's text item delimiters to \",\"\n repeat with addr in every text item of recipients\n tell newMessage to make new to recipient at end of to recipients with properties {address:addr}\n end repeat\n \n send\nend tell' && echo 'Email sent with CC'"
      mode: "local"
    timeout_ms: 30000
  - id: verify_sent
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Mail\" to get count of (every message of mailbox \"Sent Messages\")' 2>/dev/null"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Email send result:\n\nTo: {{to}}\nSubject: {{subject}}\n\nSent messages count: {{verify_sent.output}}"
    depends_on: [check_mail]
    inputs: [verify_sent.output]
---

# Email Send

Send emails via macOS Mail app.

## Usage

Send simple email:
```
to=user@example.com
subject=Hello
body=This is a test email
```

With attachment:
```
to=user@example.com
subject=Report
body=Please find the report attached
attachment=/Users/name/Documents/report.pdf
```

With CC:
```
to=user@example.com
cc=manager@example.com
subject=Meeting Notes
body=See below
```

## Parameters

- **to**: Recipient email (required)
- **subject**: Email subject (default: No Subject)
- **body**: Email body text
- **attachment**: File path to attach
- **cc**: CC recipients (comma-separated)
- **bcc**: BCC recipients (comma-separated)

## Examples

### Simple email
```
to=friend@example.com
subject=Hi there
body=Just wanted to say hi!
```

### With file
```
to=colleague@work.com
subject=Q4 Report
body=Please review the attached report
attachment=~/Documents/report.pdf
```

### Team email
```
to=all-team@company.com
cc=manager@company.com
subject=Project Update
body=Weekly status update...
```

## Notes

- Uses macOS Mail app (must be configured)
- Attachment paths must be absolute
- Comma-separate multiple CC/BCC recipients
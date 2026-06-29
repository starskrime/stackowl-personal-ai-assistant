param([Parameter(ValueFromRemainingArguments = $true)][string[]]$RalphArgs)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $PSCommandPath
. (Join-Path $here "lib/RalphLoop.ps1")

$claudeCmd = if ($env:CLAUDE_CMD) { $env:CLAUDE_CMD } else { "claude" }
$claudeModel = if ($env:CLAUDE_MODEL) { $env:CLAUDE_MODEL } else { "claude-opus-4-7" }

Invoke-RalphLoop `
    -AgentName "Claude Code" `
    -CommandName $claudeCmd `
    -Model $claudeModel `
    -YoloFlag "--dangerously-skip-permissions" `
    -BaseArgs @("-p", "--model", $claudeModel) `
    -InvocationArgs $RalphArgs

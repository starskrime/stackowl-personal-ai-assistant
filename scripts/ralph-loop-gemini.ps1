param([Parameter(ValueFromRemainingArguments = $true)][string[]]$RalphArgs)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $PSCommandPath
. (Join-Path $here "lib/RalphLoop.ps1")

$geminiCmd = if ($env:GEMINI_CMD) { $env:GEMINI_CMD } else { "gemini" }
$geminiModel = if ($env:GEMINI_MODEL) { $env:GEMINI_MODEL } else { "gemini-3.1-pro-preview" }

Invoke-RalphLoop `
    -AgentName "Gemini CLI" `
    -CommandName $geminiCmd `
    -Model $geminiModel `
    -YoloFlag "--yolo" `
    -BaseArgs @("-p", "", "-m", $geminiModel) `
    -InvocationArgs $RalphArgs

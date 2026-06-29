param([Parameter(ValueFromRemainingArguments = $true)][string[]]$RalphArgs)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $PSCommandPath
. (Join-Path $here "lib/RalphLoop.ps1")

$copilotCmd = if ($env:COPILOT_CMD) { $env:COPILOT_CMD } else { "copilot" }
$copilotModel = if ($env:COPILOT_MODEL) { $env:COPILOT_MODEL } else { "gpt-5.5" }
$copilotEffort = if ($env:COPILOT_REASONING_EFFORT) { $env:COPILOT_REASONING_EFFORT } else { "xhigh" }

Invoke-RalphLoop `
    -AgentName "GitHub Copilot CLI" `
    -CommandName $copilotCmd `
    -Model $copilotModel `
    -ReasoningEffort $copilotEffort `
    -YoloFlag "--allow-all-tools" `
    -PostPromptArgs @("--model", $copilotModel, "--reasoning-effort", $copilotEffort) `
    -InvocationArgs $RalphArgs

param([Parameter(ValueFromRemainingArguments = $true)][string[]]$RalphArgs)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $PSCommandPath
. (Join-Path $here "lib/RalphLoop.ps1")

$codexCmd = if ($env:CODEX_CMD) { $env:CODEX_CMD } else { "codex" }
$codexModel = if ($env:CODEX_MODEL) { $env:CODEX_MODEL } else { "gpt-5.5" }
$codexEffort = if ($env:CODEX_REASONING_EFFORT) { $env:CODEX_REASONING_EFFORT } else { "xhigh" }

Invoke-RalphLoop `
    -AgentName "Codex" `
    -CommandName $codexCmd `
    -Model $codexModel `
    -ReasoningEffort $codexEffort `
    -YoloFlag "--dangerously-bypass-approvals-and-sandbox" `
    -BaseArgs @("exec", "-m", $codexModel, "-c", "model_reasoning_effort=`"$codexEffort`"") `
    -InvocationArgs $RalphArgs

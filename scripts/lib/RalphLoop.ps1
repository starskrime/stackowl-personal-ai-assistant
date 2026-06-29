function Write-RalphPromptFiles {
    param([Parameter(Mandatory)][string]$ProjectDir)

    @'
# Ralph Loop - Build Mode

You are running inside a Ralph Wiggum autonomous loop (Context A).

Read `.specify/memory/constitution.md` - it contains all project principles, workflow
instructions, work sources, and completion signal requirements.

Find the highest-priority incomplete work item, implement it completely, verify all
acceptance criteria, commit and push, then output `<promise>DONE</promise>`.
'@ | Set-Content -LiteralPath (Join-Path $ProjectDir "PROMPT_build.md") -Encoding UTF8

    @'
# Ralph Loop - Planning Mode

You are running inside a Ralph Wiggum autonomous loop in planning mode.

Read `.specify/memory/constitution.md` for project principles.

Study `specs/` and compare against the current codebase (gap analysis).
Create or update `IMPLEMENTATION_PLAN.md` with a prioritized task breakdown.
Do NOT implement anything.

When the plan is complete, output `<promise>DONE</promise>`.
'@ | Set-Content -LiteralPath (Join-Path $ProjectDir "PROMPT_plan.md") -Encoding UTF8
}

function Resolve-RalphMode {
    param([string[]]$Arguments)

    $mode = "build"
    $maxIterations = 0

    if ($Arguments.Count -gt 0) {
        switch -Regex ($Arguments[0]) {
            '^(plan)$' {
                $mode = "plan"
                $maxIterations = 1
                if ($Arguments.Count -gt 1 -and $Arguments[1] -match '^\d+$') {
                    $maxIterations = [int]$Arguments[1]
                }
            }
            '^\d+$' {
                $maxIterations = [int]$Arguments[0]
            }
            default {
                throw "Unknown argument: $($Arguments[0])"
            }
        }
    }

    [pscustomobject]@{ Mode = $mode; MaxIterations = $maxIterations }
}

function Test-YoloEnabled {
    param([string]$Constitution)

    if (Test-Path -LiteralPath $Constitution -PathType Leaf) {
        return -not [bool](Select-String -LiteralPath $Constitution -Pattern 'YOLO Mode.*DISABLED' -Quiet)
    }

    return $true
}

function Invoke-RalphLoop {
    param(
        [Parameter(Mandatory)][string]$AgentName,
        [Parameter(Mandatory)][string]$CommandName,
        [Parameter(Mandatory)][string]$Model,
        [string]$ReasoningEffort = "",
        [string]$YoloFlag = "",
        [string[]]$BaseArgs = @(),
        [string[]]$PostPromptArgs = @(),
        [string[]]$InvocationArgs = @()
    )

    $scriptDir = Split-Path -Parent $PSCommandPath
    $scriptsDir = Split-Path -Parent $scriptDir
    $projectDir = Split-Path -Parent $scriptsDir
    $logDir = Join-Path $projectDir "logs"
    $constitution = Join-Path $projectDir ".specify/memory/constitution.md"

    . (Join-Path $scriptDir "SpecQueue.ps1")

    $resolved = Resolve-RalphMode -Arguments $InvocationArgs
    $mode = $resolved.Mode
    $maxIterations = $resolved.MaxIterations
    $promptName = if ($mode -eq "plan") { "PROMPT_plan.md" } else { "PROMPT_build.md" }
    $promptFile = Join-Path $projectDir $promptName

    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Set-Location -LiteralPath $projectDir
    Write-RalphPromptFiles -ProjectDir $projectDir

    if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
        throw "$AgentName CLI not found: $CommandName"
    }

    $yoloEnabled = Test-YoloEnabled -Constitution $constitution
    $summary = Get-SpecQueueSummary -SpecsDir (Join-Path $projectDir "specs")
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $currentBranch = (& git branch --show-current 2>$null)
    $ErrorActionPreference = $previousErrorActionPreference
    if (-not $currentBranch) { $currentBranch = "main" }

    $sessionLog = Join-Path $logDir ("ralph_{0}_{1}_session_{2}.log" -f ($AgentName.ToLowerInvariant() -replace '\s+', '_'), $mode, (Get-Date -Format "yyyyMMdd_HHmmss"))

    Write-Host ""
    Write-Host "RALPH LOOP ($AgentName) STARTING"
    Write-Host "Mode:   $mode"
    if ($ReasoningEffort) {
        Write-Host "Model:  $Model ($ReasoningEffort)"
    } else {
        Write-Host "Model:  $Model"
    }
    Write-Host "Prompt: $promptName"
    Write-Host "Branch: $currentBranch"
    Write-Host "YOLO:   $(if ($yoloEnabled) { 'ENABLED' } else { 'DISABLED' })"
    Write-Host "Log:    $sessionLog"

    if ($summary.HasSpecs) {
        Write-Host "Specs:  $($summary.SpecCount) total, $($summary.IncompleteSpecCount) incomplete"
        if ($summary.FirstIncompleteSpec) {
            Write-Host "Next:   $($summary.FirstIncompleteSpec)"
        }
    } else {
        Write-Host "Specs:  none found"
    }

    if ($mode -eq "build" -and $summary.HasSpecs -and $summary.IncompleteSpecCount -eq 0) {
        Write-Host "All $($summary.SpecCount) specs are COMPLETE. Nothing to do."
        return
    }

    $iteration = 0
    $consecutiveFailures = 0
    $maxConsecutiveFailures = 3

    while ($true) {
        if ($maxIterations -gt 0 -and $iteration -ge $maxIterations) {
            Write-Host "Reached max iterations: $maxIterations"
            break
        }

        $iteration++
        $logFile = Join-Path $logDir ("ralph_{0}_{1}_iter_{2}_{3}.log" -f ($AgentName.ToLowerInvariant() -replace '\s+', '_'), $mode, $iteration, (Get-Date -Format "yyyyMMdd_HHmmss"))
        $outputFile = Join-Path $logDir ("ralph_{0}_output_iter_{1}_{2}.txt" -f ($AgentName.ToLowerInvariant() -replace '\s+', '_'), $iteration, (Get-Date -Format "yyyyMMdd_HHmmss"))
        $prompt = Get-Content -LiteralPath $promptFile -Raw

        Write-Host ""
        Write-Host "LOOP $iteration"

        $args = @()
        $args += $BaseArgs
        if ($yoloEnabled -and $YoloFlag) {
            $args += $YoloFlag
        }
        $args += $PostPromptArgs

        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        if ($CommandName -eq "codex") {
            $args += @("-", "--output-last-message", $outputFile)
            $output = $prompt | & $CommandName @args 2>&1 | Tee-Object -FilePath $logFile
        } elseif ($CommandName -eq "copilot") {
            $args = @("-p", $prompt) + $args
            $output = & $CommandName @args 2>&1 | Tee-Object -FilePath $logFile
        } else {
            $output = $prompt | & $CommandName @args 2>&1 | Tee-Object -FilePath $logFile
        }
        $agentExitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousErrorActionPreference

        $text = ($output | Out-String)
        if (Test-Path -LiteralPath $outputFile -PathType Leaf) {
            $text += "`n" + (Get-Content -LiteralPath $outputFile -Raw)
        }

        if ($agentExitCode -eq 0 -and $text -match '<promise>(ALL_)?DONE</promise>') {
            Write-Host "$AgentName execution completed and completion signal was detected."
            $consecutiveFailures = 0
            if ($mode -eq "plan") { break }
        } else {
            Write-Warning "$AgentName did not produce a completion signal. See $logFile"
            $consecutiveFailures++
            if ($consecutiveFailures -ge $maxConsecutiveFailures) {
                Write-Warning "$maxConsecutiveFailures consecutive iterations without completion."
                $consecutiveFailures = 0
            }
        }

        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & git push origin $currentBranch 2>$null | Out-Null
        $ErrorActionPreference = $previousErrorActionPreference
        Start-Sleep -Seconds 2
    }
}

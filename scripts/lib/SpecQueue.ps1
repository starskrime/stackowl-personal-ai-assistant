function Get-RootSpecs {
    param([string]$SpecsDir = "specs")

    if (-not (Test-Path -LiteralPath $SpecsDir -PathType Container)) {
        return @()
    }

    Get-ChildItem -LiteralPath $SpecsDir -File -Filter "*.md" |
        Sort-Object -Property Name
}

function Test-RootSpecComplete {
    param([Parameter(Mandatory)][string]$SpecFile)

    if (-not (Test-Path -LiteralPath $SpecFile -PathType Leaf)) {
        return $false
    }

    $pattern = '^(#{1,3} )?(\*\*)?Status(\*\*)?:\s+COMPLETE'
    return [bool](Select-String -LiteralPath $SpecFile -Pattern $pattern -Quiet)
}

function Get-IncompleteRootSpecs {
    param([string]$SpecsDir = "specs")

    @(Get-RootSpecs -SpecsDir $SpecsDir | Where-Object {
        -not (Test-RootSpecComplete -SpecFile $_.FullName)
    })
}

function Get-SpecQueueSummary {
    param([string]$SpecsDir = "specs")

    $specs = @(Get-RootSpecs -SpecsDir $SpecsDir)
    $incomplete = @(Get-IncompleteRootSpecs -SpecsDir $SpecsDir)

    [pscustomobject]@{
        HasSpecs = $specs.Count -gt 0
        SpecCount = $specs.Count
        IncompleteSpecCount = $incomplete.Count
        FirstIncompleteSpec = if ($incomplete.Count -gt 0) { $incomplete[0].FullName } else { "" }
    }
}

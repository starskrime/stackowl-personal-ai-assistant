#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Installs StackOwl as a Windows service using NSSM.
.DESCRIPTION
    Downloads NSSM if not found in PATH, verifies the SHA-256 checksum,
    installs the StackOwl service, configures restart behaviour, and starts it.

    IMPORTANT: Replace NSSM_EXPECTED_SHA256 with the real checksum before deployment.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ServiceName   = "StackOwl"
$NssmVersion   = "2.24"
$NssmUrl       = "https://nssm.cc/release/nssm-$NssmVersion.zip"
# TODO: Replace with real SHA-256 before deployment
$NssmExpectedSha256 = "PLACEHOLDER_REAL_SHA256_MUST_BE_SET_BEFORE_DEPLOYMENT"

$AppDir = if ($env:STACKOWL_HOME) { $env:STACKOWL_HOME } else { Join-Path $env:APPDATA "StackOwl" }
$StackOwlExe = "C:\Program Files\StackOwl\stackowl.exe"

# ------------------------------------------------------------------
# 1. Resolve NSSM
# ------------------------------------------------------------------
Write-Host "[stackowl] Checking for NSSM in PATH..."
$NssmExe = Get-Command nssm -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source

if (-not $NssmExe) {
    Write-Host "[stackowl] NSSM not found — downloading from $NssmUrl"
    $TempDir  = Join-Path ([System.IO.Path]::GetTempPath()) "nssm-install-$([System.Guid]::NewGuid().ToString('N'))"
    $ZipPath  = Join-Path $TempDir "nssm.zip"
    $ExtractDir = Join-Path $TempDir "nssm"

    New-Item -ItemType Directory -Path $TempDir -Force | Out-Null

    # Download
    Invoke-WebRequest -Uri $NssmUrl -OutFile $ZipPath -UseBasicParsing

    # Verify SHA-256
    $ActualHash = (Get-FileHash -Path $ZipPath -Algorithm SHA256).Hash
    if ($ActualHash -ne $NssmExpectedSha256) {
        Write-Error "[stackowl] NSSM checksum mismatch. Expected: $NssmExpectedSha256  Got: $ActualHash"
        exit 1
    }
    Write-Host "[stackowl] SHA-256 verified OK"

    # Extract
    Expand-Archive -Path $ZipPath -DestinationPath $ExtractDir -Force

    # Locate the correct architecture binary
    $Arch = if ([Environment]::Is64BitOperatingSystem) { "win64" } else { "win32" }
    $NssmExe = Get-ChildItem -Path $ExtractDir -Recurse -Filter "nssm.exe" |
               Where-Object { $_.FullName -like "*$Arch*" } |
               Select-Object -First 1 -ExpandProperty FullName

    if (-not $NssmExe) {
        Write-Error "[stackowl] Could not locate nssm.exe in extracted archive"
        exit 1
    }

    Write-Host "[stackowl] NSSM resolved at $NssmExe"
}

# ------------------------------------------------------------------
# 2. Ensure AppDirectory exists
# ------------------------------------------------------------------
if (-not (Test-Path $AppDir)) {
    Write-Host "[stackowl] Creating AppDirectory: $AppDir"
    New-Item -ItemType Directory -Path $AppDir -Force | Out-Null
}

# ------------------------------------------------------------------
# 3. Install or update service
# ------------------------------------------------------------------
$Existing = & $NssmExe status $ServiceName 2>&1
if ($LASTEXITCODE -eq 0 -and $Existing -notmatch "can't open service") {
    Write-Host "[stackowl] Stopping existing service before reconfiguration..."
    & $NssmExe stop $ServiceName confirm 2>&1 | Out-Null
    & $NssmExe remove $ServiceName confirm 2>&1 | Out-Null
}

Write-Host "[stackowl] Installing service '$ServiceName'..."
& $NssmExe install $ServiceName $StackOwlExe serve
if ($LASTEXITCODE -ne 0) {
    Write-Error "[stackowl] NSSM install failed (exit code $LASTEXITCODE)"
    exit 1
}

# ------------------------------------------------------------------
# 4. Configure service parameters
# ------------------------------------------------------------------
& $NssmExe set $ServiceName AppDirectory $AppDir
& $NssmExe set $ServiceName AppEnvironmentExtra "STACKOWL_HOME=$AppDir"
& $NssmExe set $ServiceName AppRestartDelay 5000
& $NssmExe set $ServiceName AppStopMethodSkip 0
& $NssmExe set $ServiceName AppKillProcessTree 1
& $NssmExe set $ServiceName Description "StackOwl Personal AI Assistant"
& $NssmExe set $ServiceName Start SERVICE_AUTO_START

# Logging
$LogDir = Join-Path $AppDir "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
& $NssmExe set $ServiceName AppStdout (Join-Path $LogDir "stdout.log")
& $NssmExe set $ServiceName AppStderr (Join-Path $LogDir "stderr.log")
& $NssmExe set $ServiceName AppRotateFiles 1
& $NssmExe set $ServiceName AppRotateBytes 10485760  # 10 MB

# ------------------------------------------------------------------
# 5. Start the service
# ------------------------------------------------------------------
Write-Host "[stackowl] Starting service '$ServiceName'..."
& $NssmExe start $ServiceName
if ($LASTEXITCODE -ne 0) {
    Write-Warning "[stackowl] Service start returned exit code $LASTEXITCODE — check Windows Event Log"
} else {
    Write-Host "[stackowl] Service '$ServiceName' started successfully"
}

Write-Host ""
Write-Host "✓ StackOwl service installed"
Write-Host "  AppDirectory : $AppDir"
Write-Host "  Executable   : $StackOwlExe"
Write-Host "  Manage with  : nssm {start|stop|restart|status} $ServiceName"

param(
    [string]$TargetDir = (Get-Location).Path
)

$ErrorActionPreference = "Stop"

Write-Host "Installing memory-hook into $TargetDir..." -ForegroundColor Cyan

if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) {
    Write-Error "Python 3 is required to install memory-hook."
    exit 1
}

$InstallPyUrl = "https://raw.githubusercontent.com/subhasishsaha/memory-hook-for-agents/main/install.py"
$ScriptContent = (Invoke-RestMethod -Uri $InstallPyUrl -UseBasicParsing)
$ScriptContent | python - $TargetDir

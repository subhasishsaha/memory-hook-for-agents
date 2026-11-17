param(
    [string]$TargetDir = (Get-Location).Path
)

$ErrorActionPreference = "Stop"

Write-Host "Installing memory-hook into $TargetDir..." -ForegroundColor Cyan

if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) {
    Write-Error "Python 3 is required to install memory-hook."
    exit 1
}

python -m pip install --quiet memory-hook
python -m memory_hook.cli install $TargetDir

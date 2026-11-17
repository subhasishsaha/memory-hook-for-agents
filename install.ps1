# Universal one-line installer for Windows PowerShell
param (
    [string]$TargetDir = (Get-Location).Path
)

$ErrorActionPreference = "Stop"

Write-Host "Installing memory-hook into $TargetDir..." -ForegroundColor Cyan

$PythonCmd = "python"
if (-not (Get-Command $PythonCmd -ErrorAction SilentlyContinue)) {
    Write-Error "Python 3 is required to install memory-hook."
    exit 1
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& $PythonCmd "$ScriptDir\install.py" $TargetDir

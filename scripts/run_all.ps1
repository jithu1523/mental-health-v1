$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot

Set-Location $repoRoot

Write-Host "Starting backend in a new PowerShell window..."
Start-Process -FilePath "powershell.exe" -ArgumentList "-NoExit", "-File", (Join-Path $repoRoot "scripts\run_backend.ps1")

Start-Sleep -Seconds 2

Write-Host "Starting frontend in a new PowerShell window..."
Start-Process -FilePath "powershell.exe" -ArgumentList "-NoExit", "-File", (Join-Path $repoRoot "scripts\run_frontend.ps1")

Write-Host ""
Write-Host "Windows dev windows opened."

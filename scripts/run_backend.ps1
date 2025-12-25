$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "mindtriage\backend"
$backendVenv = Join-Path $backendDir ".venv"

Set-Location $backendDir

if (-not (Test-Path $backendVenv)) {
    Write-Host "Creating backend venv..."
    python -m venv $backendVenv
}

Write-Host "Installing backend requirements..."
& "$backendVenv\Scripts\python.exe" -m pip install -r "requirements.txt" | Out-Null

Write-Host "Starting backend..."
Write-Host "  API: http://127.0.0.1:8000"
Write-Host "  Docs: http://127.0.0.1:8000/docs"
Write-Host ""
Write-Host "Tip: set DEV_MODE=1 for local dev overrides if needed."

& "$backendVenv\Scripts\python.exe" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

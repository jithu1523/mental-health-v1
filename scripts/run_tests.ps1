$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "mindtriage\backend"
$backendVenv = Join-Path $backendDir ".venv"

if (-not (Test-Path $backendVenv)) {
    Write-Host "Creating backend venv..."
    python -m venv $backendVenv
}

Write-Host "Installing backend test requirements..."
& "$backendVenv\Scripts\python.exe" -m pip install -r (Join-Path $backendDir "requirements-dev.txt") | Out-Null

Write-Host "Running pytest..."
& "$backendVenv\Scripts\python.exe" -m pytest (Join-Path $backendDir "tests")

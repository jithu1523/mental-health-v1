$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$frontendDir = Join-Path $repoRoot "mindtriage\frontend"
$frontendVenv = Join-Path $frontendDir ".venv"

Set-Location $frontendDir

if (-not (Test-Path $frontendVenv)) {
    Write-Host "Creating frontend venv..."
    python -m venv $frontendVenv
}

Write-Host "Installing frontend requirements..."
& "$frontendVenv\Scripts\python.exe" -m pip install -r "requirements.txt" | Out-Null

Write-Host "Starting frontend..."
Write-Host "  UI: http://localhost:8501"
Write-Host ""
Write-Host "Tip: use ?dev=1&dev_key=... only when DEV_MODE=1 is set."

& "$frontendVenv\Scripts\python.exe" -m streamlit run streamlit_app.py --server.port 8501

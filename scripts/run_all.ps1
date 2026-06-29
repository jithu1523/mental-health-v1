$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendDir = Join-Path $repoRoot "mindtriage\backend"
$frontendDir = Join-Path $repoRoot "mindtriage\frontend"
$backendActivate = Join-Path $backendDir ".venv\Scripts\Activate.ps1"
$frontendActivate = Join-Path $frontendDir ".venv\Scripts\Activate.ps1"

if (-not (Test-Path $backendActivate)) {
    Write-Host "Backend venv not found. Run .\scripts\run_backend.ps1 first."
    exit 1
}
if (-not (Test-Path $frontendActivate)) {
    Write-Host "Frontend venv not found. Run .\scripts\run_frontend.ps1 first."
    exit 1
}

Write-Host "Starting backend in a new PowerShell window..."
Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-NoExit",
    "-Command", "cd `"$backendDir`"; . `"$backendActivate`"; uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"
)

Start-Sleep -Seconds 2

Write-Host "Starting frontend in a new PowerShell window..."
Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-NoExit",
    "-Command", "cd `"$frontendDir`"; . `"$frontendActivate`"; streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501"
)

Write-Host ""
Write-Host "Windows dev windows opened."

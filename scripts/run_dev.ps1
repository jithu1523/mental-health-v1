$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "mindtriage\backend"
$frontendDir = Join-Path $repoRoot "mindtriage\frontend"

$backendVenv = Join-Path $backendDir ".venv"
$frontendVenv = Join-Path $frontendDir ".venv"

if (-not (Test-Path $backendVenv)) {
    Write-Host "Creating backend venv..."
    python -m venv $backendVenv
}

Write-Host "Installing backend requirements..."
& "$backendVenv\Scripts\python.exe" -m pip install -r (Join-Path $backendDir "requirements.txt") | Out-Null

Write-Host "Starting backend on http://127.0.0.1:8000 ..."
Start-Process -FilePath "$backendVenv\Scripts\python.exe" `
    -ArgumentList "-m uvicorn app.main:app --reload --port 8000" `
    -WorkingDirectory $backendDir `
    -WindowStyle Normal

$maxWait = 60
for ($i = 0; $i -lt $maxWait; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) {
            Write-Host "Backend is ready."
            break
        }
    } catch {
        Start-Sleep -Seconds 1
    }
}

if (-not (Test-Path $frontendVenv)) {
    Write-Host "Creating frontend venv..."
    python -m venv $frontendVenv
}

Write-Host "Installing frontend requirements..."
& "$frontendVenv\Scripts\python.exe" -m pip install -r (Join-Path $frontendDir "requirements.txt") | Out-Null

Write-Host "Starting Streamlit on http://localhost:8501 ..."
Start-Process -FilePath "$frontendVenv\Scripts\python.exe" `
    -ArgumentList "-m streamlit run streamlit_app.py --server.port 8501" `
    -WorkingDirectory $frontendDir `
    -WindowStyle Normal

Write-Host ""
Write-Host "MindTriage URLs:"
Write-Host "  Backend: http://127.0.0.1:8000/docs"
Write-Host "  Frontend: http://localhost:8501"
Write-Host ""
Write-Host "If the frontend cannot connect, confirm the backend is running and port 8000 is free."

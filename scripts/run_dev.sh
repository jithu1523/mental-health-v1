#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend_dir="${repo_root}/mindtriage/backend"
frontend_dir="${repo_root}/mindtriage/frontend"

backend_venv="${backend_dir}/.venv"
frontend_venv="${frontend_dir}/.venv"

if [ ! -d "${backend_venv}" ]; then
  echo "Creating backend venv..."
  python3 -m venv "${backend_venv}"
fi

echo "Installing backend requirements..."
"${backend_venv}/bin/python" -m pip install -r "${backend_dir}/requirements.txt" >/dev/null

echo "Starting backend on http://127.0.0.1:8000 ..."
(
  cd "${backend_dir}"
  "${backend_venv}/bin/python" -m uvicorn app.main:app --reload --port 8000
) &
backend_pid=$!

cleanup() {
  if kill -0 "${backend_pid}" >/dev/null 2>&1; then
    kill "${backend_pid}"
  fi
}
trap cleanup EXIT

for _ in {1..60}; do
  if curl -sSf "http://127.0.0.1:8000/health" >/dev/null; then
    echo "Backend is ready."
    break
  fi
  sleep 1
done

if [ ! -d "${frontend_venv}" ]; then
  echo "Creating frontend venv..."
  python3 -m venv "${frontend_venv}"
fi

echo "Installing frontend requirements..."
"${frontend_venv}/bin/python" -m pip install -r "${frontend_dir}/requirements.txt" >/dev/null

echo "Starting Streamlit on http://localhost:8501 ..."
cd "${frontend_dir}"
"${frontend_venv}/bin/python" -m streamlit run streamlit_app.py --server.port 8501

echo ""
echo "MindTriage URLs:"
echo "  Backend: http://127.0.0.1:8000/docs"
echo "  Frontend: http://localhost:8501"
echo ""
echo "If the frontend cannot connect, confirm the backend is running and port 8000 is free."
